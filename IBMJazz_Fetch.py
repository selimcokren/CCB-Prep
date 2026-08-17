import re
import html
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

# Disable SSL warnings for internal servers with self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION ---
BASE_CCM_URL = "https://rb-alm-06-p.de.bosch.com/ccm"
WORK_ITEM_ID = "3785991"     # Replace with your target Work Item ID

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


def print_work_item_data(data, wi_id=None, indent=""):
    """Print a formatted work item summary."""
    numeric_id = wi_id or data.get('dcterms:identifier', '?')
    print(f"{indent}ID:          {numeric_id}")
    print(f"{indent}Title:       {data.get('dcterms:title', 'No Title')}")
    print(f"{indent}Status:      {resolve_status(data.get('rtc_cm:state', {}))}")
    tags = data.get('dcterms:subject', '')
    if isinstance(tags, list):
        tags = ', '.join(tags)
    print(f"{indent}Tags:        {tags or 'None'}")
    desc = strip_html(data.get('dcterms:description', '') or '')
    if desc:
        desc_indented = '\n'.join(f"{indent}  {line}" for line in desc.splitlines())
        print(f"{indent}Description:\n{desc_indented}")


def fetch_and_print_linked(session, data, depth=0, visited=None):
    """Recursively fetch and print linked work items up to MAX_LINK_DEPTH."""
    if visited is None:
        visited = set()
    if depth >= MAX_LINK_DEPTH:
        return

    links = extract_links(data)
    indent = "  " * (depth + 1)
    sep = "-" * max(20, 62 - len(indent))

    if not links:
        if depth == 0:
            print(f"{indent}No linked work items found in known link types.")
            if DEBUG_LINKS:
                all_keys = [k for k in data.keys() if ':' in k and not k.startswith('dcterms:') and not k.startswith('rdf:')]
                print(f"{indent}[DEBUG] Non-standard keys in response (potential link keys):")
                for k in all_keys:
                    print(f"{indent}  {k}: {data[k]}")
        return

    headers = {
        "OSLC-Core-Version": "2.0",
        "Accept": "application/json; charset=utf-8"
    }

    for label, uris in links.items():
        print(f"\n{indent}[ {label} ]  {len(uris)} item(s)")
        for uri in uris:
            if uri in visited:
                print(f"{indent}  ↩ Already fetched: {uri}")
                continue
            visited.add(uri)

            fetch_url = uri_to_oslc_url(uri)
            print(f"{indent}  → Fetching: {fetch_url} ...")
            response = session.get(fetch_url, headers=headers)
            if response.status_code == 200:
                try:
                    linked_data = response.json()
                    numeric_id = linked_data.get('dcterms:identifier', fetch_url.rstrip('/').split('/')[-1])
                    print(f"{indent}{sep}")
                    print(f"{indent}  Linked Work Item  #{numeric_id}")
                    print(f"{indent}{sep}")
                    print_work_item_data(linked_data, wi_id=numeric_id, indent=f"{indent}  ")
                    print(f"{indent}{sep}")
                    fetch_and_print_linked(session, linked_data, depth + 1, visited)
                except Exception as e:
                    print(f"{indent}  ❌ Failed to parse response: {e}")
            else:
                print(f"{indent}  ❌ HTTP {response.status_code} — {fetch_url}")


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
        authenticated_user = USERNAME  # fallback if body is empty
    print(f"  Authenticated as: {authenticated_user}")

    # Fetch the Work Item
    print(f"Step 2: Fetching Work Item #{WORK_ITEM_ID}...")
    work_item_url = f"{BASE_CCM_URL}/oslc/workitems/{WORK_ITEM_ID}"
    headers = {
        "OSLC-Core-Version": "2.0",
        "Accept": "application/json; charset=utf-8"
    }
    response = session.get(work_item_url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        sep = "=" * 64
        print(f"\n{sep}")
        print(f"  WORK ITEM  #{WORK_ITEM_ID}")
        print(sep)
        print_work_item_data(data, wi_id=WORK_ITEM_ID)
        print(sep)
        if FETCH_LINKED:
            print("\nStep 3: Fetching linked work items...")
            fetch_and_print_linked(session, data)
            print("\nDone.")
    else:
        print(f"❌ Failed to fetch work item. HTTP Status: {response.status_code}")
        print(response.text[:500])

if __name__ == "__main__":
    authenticate_and_fetch()
