import re
import html
import argparse
import requests
import urllib3
import keyring


def strip_html(text):
    """Convert HTML to plain text."""
    text = html.unescape(text or "")
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def resolve_status(state):
    """Extract status label from rtc_cm:state regardless of server format."""
    if isinstance(state, dict):
        return state.get('dcterms:title') or state.get('rdf:resource', 'Unknown').split('/')[-1]
    if isinstance(state, str):
        return state.split('/')[-1]
    return 'Unknown'


def resolve_type(data):
    """Best-effort extraction of the work item type label from OSLC JSON.

    RTC servers vary in how they expose the type. We try multiple candidate
    fields and fall back to 'Unknown' if none yields a useful value.
    """
    candidates = [
        data.get('dcterms:type'),
        data.get('rtc_cm:type'),
        data.get('oslc_cm:type'),
    ]
    for val in candidates:
        if isinstance(val, dict):
            label = val.get('dcterms:title') or val.get('rdf:resource', '').rstrip('/').split('/')[-1]
            if label:
                return label
        elif isinstance(val, str) and val:
            return val.rstrip('/').split('/')[-1]
    # Last resort: scan rdf:type list for a work-item-type hint
    for entry in (data.get('rdf:type') or []):
        if isinstance(entry, dict):
            uri = entry.get('rdf:resource', '')
            if 'workitemtype' in uri.lower():
                return uri.rstrip('/').split('/')[-1]
    return 'Unknown'

# Disable SSL warnings for internal servers with self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION ---
BASE_CCM_URL = "https://rb-alm-06-p.de.bosch.com/ccm"

# Single WI or batch: list one or more Work Item IDs to fetch
WORK_ITEM_IDS = ["3785991"]   # Add more IDs for batch: ["123", "456", "789"]

# Output file: set to a file path to also write results to disk (None = console only)
# Example: OUTPUT_FILE = "ccb_batch_output.txt"
OUTPUT_FILE = None

# Credentials stored in Windows Credential Manager — never in code or terminal
# Directly go to Windows Credential Manager and add a new Generic Credential with:
#   Internet or network address: bosch_jazz
#   Username: <your_username> e.g: xyz3abt
#   Password: <your_password> e.g: NT password
credential = keyring.get_credential("bosch_jazz", None)
USERNAME = credential.username if credential else ""
PASSWORD = credential.password if credential else ""

# Link types to follow when fetching related work items (actual RTC OSLC JSON keys)
LINK_TYPES = {
    "rtc_cm:com.ibm.team.workitem.linktype.resolvesworkitem.resolves":   "Resolves (Defect)",
    "rtc_cm:com.ibm.team.workitem.linktype.resolvesworkitem.resolvedBy": "Resolved By",
    "rtc_cm:com.ibm.team.workitem.linktype.relatedworkitem.related":     "Related",
    "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.children":     "Children",
    "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.parent":       "Parent",
    "rtc_cm:com.ibm.team.workitem.linktype.copiedworkitem.copiedFrom":   "Copied From",
    "oslc_cm:affectsRequirement":                                        "Affects Requirement",
    "oslc_cm:tracksRequirement":                                         "Tracks Requirement",
    "oslc_cm:implementsRequirement":                                     "Implements Requirement",
    "oslc_cm:affectedByDefect":                                          "Affected By Defect",
}
FETCH_LINKED = True   # Set to False to skip linked item fetching
MAX_LINK_DEPTH = 2    # 1 = direct links only; 2 = links of links; etc.
DEBUG_LINKS = False   # Set to True to dump all JSON keys when no links are found


def _is_workitem_uri(uri):
    """Return True only for CCM work item URIs — blocks external URLs (RM, Docupedia, etc.)."""
    if not uri or not isinstance(uri, str):
        return False
    return '/oslc/workitems/' in uri or '/com.ibm.team.workitem.WorkItem/' in uri


def extract_links(data):
    """Extract linked CCM work item URIs from a work item JSON payload."""
    links = {}
    for key, label in LINK_TYPES.items():
        value = data.get(key)
        if not value:
            continue
        if isinstance(value, list):
            uris = [v['rdf:resource'] for v in value
                    if isinstance(v, dict) and 'rdf:resource' in v and _is_workitem_uri(v['rdf:resource'])]
        elif isinstance(value, dict) and 'rdf:resource' in value and _is_workitem_uri(value['rdf:resource']):
            uris = [value['rdf:resource']]
        elif isinstance(value, str) and _is_workitem_uri(value):
            uris = [value]
        else:
            uris = []
        if uris:
            links[label] = uris
    return links


def uri_to_oslc_url(uri):
    """Convert any RTC resource URI to an OSLC-fetchable URL."""
    if '/oslc/workitems/' in uri:
        return uri
    last_segment = uri.rstrip('/').split('/')[-1]
    if last_segment.isdigit():
        return f"{BASE_CCM_URL}/oslc/workitems/{last_segment}"
    return uri  # OID-based or external URI — fetch directly


def format_work_item_data(data, wi_id=None, indent=""):
    """Return a formatted work item summary as a string."""
    lines = []
    numeric_id = wi_id or data.get('dcterms:identifier', '?')
    lines.append(f"{indent}ID:          {numeric_id}")
    lines.append(f"{indent}Title:       {data.get('dcterms:title', 'No Title')}")
    lines.append(f"{indent}Type:        {resolve_type(data)}")
    lines.append(f"{indent}Status:      {resolve_status(data.get('rtc_cm:state', {}))}")
    tags = data.get('dcterms:subject', '')
    if isinstance(tags, list):
        tags = ', '.join(tags)
    lines.append(f"{indent}Tags:        {tags or 'None'}")
    desc = strip_html(data.get('dcterms:description', '') or '')
    if desc:
        desc_indented = '\n'.join(f"{indent}  {line}" for line in desc.splitlines())
        lines.append(f"{indent}Description:\n{desc_indented}")
    return '\n'.join(lines)


def print_work_item_data(data, wi_id=None, indent=""):
    """Print a formatted work item summary."""
    print(format_work_item_data(data, wi_id=wi_id, indent=indent))


def collect_linked_text(session, data, depth=0, visited=None):
    """Recursively collect linked work item text up to MAX_LINK_DEPTH. Returns string."""
    if visited is None:
        visited = set()
    if depth >= MAX_LINK_DEPTH:
        return ""

    links = extract_links(data)
    indent = "  " * (depth + 1)
    sep = "-" * max(20, 62 - len(indent))
    lines = []

    if not links:
        if depth == 0:
            lines.append(f"{indent}No linked work items found in known link types.")
            if DEBUG_LINKS:
                all_keys = [k for k in data.keys() if ':' in k and not k.startswith('dcterms:') and not k.startswith('rdf:')]
                lines.append(f"{indent}[DEBUG] Non-standard keys in response (potential link keys):")
                for k in all_keys:
                    lines.append(f"{indent}  {k}: {data[k]}")
        return '\n'.join(lines)

    headers = {
        "OSLC-Core-Version": "2.0",
        "Accept": "application/json; charset=utf-8"
    }

    for label, uris in links.items():
        lines.append(f"\n{indent}[ {label} ]  {len(uris)} item(s)")
        for uri in uris:
            if uri in visited:
                lines.append(f"{indent}  ↩ Already fetched: {uri}")
                continue
            visited.add(uri)

            fetch_url = uri_to_oslc_url(uri)
            lines.append(f"{indent}  → Fetching: {fetch_url} ...")
            response = session.get(fetch_url, headers=headers)
            if response.status_code == 200:
                try:
                    linked_data = response.json()
                    numeric_id = linked_data.get('dcterms:identifier', fetch_url.rstrip('/').split('/')[-1])
                    lines.append(f"{indent}{sep}")
                    lines.append(f"{indent}  Linked Work Item  #{numeric_id}")
                    lines.append(f"{indent}{sep}")
                    lines.append(format_work_item_data(linked_data, wi_id=numeric_id, indent=f"{indent}  "))
                    lines.append(f"{indent}{sep}")
                    lines.append(collect_linked_text(session, linked_data, depth + 1, visited))
                except Exception as e:
                    lines.append(f"{indent}  ❌ Failed to parse response: {e}")
            else:
                lines.append(f"{indent}  ❌ HTTP {response.status_code} — {fetch_url}")

    return '\n'.join(lines)


def fetch_and_print_linked(session, data, depth=0, visited=None):
    """Recursively fetch and print linked work items up to MAX_LINK_DEPTH."""
    text = collect_linked_text(session, data, depth=depth, visited=visited)
    if text:
        print(text)


def fetch_single_work_item(session, wi_id):
    """Fetch one work item and return its formatted text block. Returns None on failure."""
    headers = {
        "OSLC-Core-Version": "2.0",
        "Accept": "application/json; charset=utf-8"
    }
    url = f"{BASE_CCM_URL}/oslc/workitems/{wi_id}"
    response = session.get(url, headers=headers)
    if response.status_code != 200:
        return None, f"❌ HTTP {response.status_code} — {url}\n{response.text[:500]}"

    data = response.json()
    sep = "=" * 64
    lines = []
    lines.append(f"\n{sep}")
    lines.append(f"--- Work Item Data ---")
    lines.append(sep)
    lines.append(format_work_item_data(data, wi_id=wi_id))
    lines.append(sep)

    if FETCH_LINKED:
        linked_text = collect_linked_text(session, data)
        if linked_text:
            lines.append("\nLinked Work Items:")
            lines.append(linked_text)

    return data, '\n'.join(lines)


def authenticate_and_fetch():
    if not USERNAME or not PASSWORD:
        print("❌ No credentials found in Windows Credential Manager.")
        print("   Run once to store them:")
        print("   python -c \"import keyring; keyring.set_password('bosch_jazz', 'your_username', 'your_password')\"")
        return

    # Basic Auth — directly supported by JSA (www-authenticate: Basic realm="JSA")
    session = requests.Session()
    session.verify = False
    session.auth = (USERNAME, PASSWORD)

    # Verify authentication
    print("Step 1: Verifying authentication...")
    whoami_res = session.get(f"{BASE_CCM_URL}/whoami")
    if whoami_res.status_code != 200:
        print(f"❌ Authentication failed. HTTP Status: {whoami_res.status_code}")
        return
    try:
        authenticated_user = whoami_res.json().get("userId", "unknown")
    except Exception:
        authenticated_user = USERNAME
    print(f"  Authenticated as: {authenticated_user}")

    all_output = []
    total = len(WORK_ITEM_IDS)

    for idx, wi_id in enumerate(WORK_ITEM_IDS, start=1):
        print(f"\nStep 2 [{idx}/{total}]: Fetching Work Item #{wi_id}...")
        _data, text = fetch_single_work_item(session, wi_id)
        print(text)
        all_output.append(text)
        if _data is None:
            continue
        if FETCH_LINKED and total == 1:
            # linked items already included in text for single-WI runs via fetch_single_work_item
            pass

    if OUTPUT_FILE:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(all_output))
        print(f"\n✅ Output written to: {OUTPUT_FILE}")

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch IBM Jazz/RTC work items.")
    parser.add_argument("--ids", nargs="+", metavar="ID",
                        help="One or more Work Item IDs to fetch (overrides WORK_ITEM_IDS)")
    parser.add_argument("--output", metavar="FILE",
                        help="Write output to this file in addition to console (overrides OUTPUT_FILE)")
    args = parser.parse_args()
    if args.ids:
        WORK_ITEM_IDS = args.ids
    if args.output:
        OUTPUT_FILE = args.output
    authenticate_and_fetch()
