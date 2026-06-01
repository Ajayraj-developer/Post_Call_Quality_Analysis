import os
import io
import re
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from azure.storage.blob import BlobServiceClient, ContainerClient
from dotenv import load_dotenv

# Load .env from project root so this module can be used standalone
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path)

def _normalize_sas_token(sas_token):
    if not sas_token:
        return None
    sas_token = sas_token.strip()
    if sas_token.startswith('?'):
        sas_token = sas_token[1:]
    return sas_token


def _validate_sv_from_query(query):
    # query is the raw query string or a dict-like mapping
    from urllib.parse import parse_qs
    if isinstance(query, str):
        qs = parse_qs(query)
    else:
        qs = query
    sv_list = qs.get('sv') or qs.get('SV') or qs.get('Sv')
    if not sv_list:
        raise ValueError("SAS token missing 'sv' (service version) parameter.")
    sv = sv_list[0]
    # Azure service version is typically YYYY-MM-DD
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", sv):
        raise ValueError(f"SAS field 'sv' is not well formed: {sv!r}")
    return sv


def _build_container_client(storage_account_name, container_name, sas_token, sas_url=None):
    sas_token = _normalize_sas_token(sas_token)
    if sas_url:
        sas_url = sas_url.strip()
        parsed = urlparse(sas_url)
        query = parsed.query
        path = parsed.path

        # If URL has no container path but we have a container name, append it.
        if container_name and (not path or path == '/'):
            path = f'/{container_name}'

        # If the URL is a container/account URL and the token is separate, attach it.
        if sas_token and not query:
            query = sas_token
        elif sas_token and query:
            # preserve query and append token if not already present
            parsed_qs = parse_qs(query)
            if not any(key.startswith('sv') for key in parsed_qs):
                query = query + '&' + sas_token

        # validate the sv parameter early so we can return a clear error
        try:
            _validate_sv_from_query(query)
        except ValueError as e:
            raise ValueError(f"Invalid SAS URL: {e}")

        sas_url = urlunparse(parsed._replace(path=path, query=query))
        return ContainerClient.from_container_url(sas_url)

    if not storage_account_name or not container_name or not sas_token:
        raise ValueError('Missing Azure storage account, container name, or SAS token for cloud sync.')

    blob_service_client = BlobServiceClient(
        account_url=f"https://{storage_account_name}.blob.core.windows.net",
        credential=sas_token
    )
    # validate the sas_token provided as raw query string
    try:
        _validate_sv_from_query(sas_token)
    except ValueError as e:
        raise ValueError(f"Invalid SAS token: {e}")

    return blob_service_client.get_container_client(container_name)


def sync_all_calls_from_azure(storage_account_name=None, container_name=None, sas_token=None, download_dir=None, sas_url=None):
    # Allow callers to omit parameters and fall back to environment (.env) values
    storage_account_name = storage_account_name or os.environ.get('AZURE_STORAGE_ACCOUNT_NAME') or os.environ.get('VITE_AZURE_STORAGE_ACCOUNT_NAME')
    container_name = container_name or os.environ.get('AZURE_STORAGE_CONTAINER_NAME') or os.environ.get('VITE_AZURE_STORAGE_CONTAINER_NAME')
    sas_token = sas_token or os.environ.get('AZURE_STORAGE_SAS_TOKEN') or os.environ.get('VITE_AZURE_STORAGE_SAS_TOKEN')
    sas_url = sas_url or os.environ.get('AZURE_STORAGE_SAS_URL') or os.environ.get('VITE_AZURE_STORAGE_SAS_URL')

    if not download_dir:
        download_dir = os.path.join(os.path.dirname(__file__), 'Cloud_sync', 'downloaded_transcripts')

    container_client = _build_container_client(storage_account_name, container_name, sas_token, sas_url=sas_url)
    os.makedirs(download_dir, exist_ok=True)

    call_files = {}
    for blob in container_client.list_blobs():
        base_name = blob.name.split('.')[0].replace('_agent', '').replace('_emp', '')
        match = re.search(r"(\d+)", base_name)
        call_id = match.group(1) if match else base_name
        call_files.setdefault(call_id, []).append(blob.name)

    results = []
    for call_id, files in call_files.items():
        transcript = agent_audio = emp_audio = None
        for fname in files:
            download_path = os.path.join(download_dir, os.path.basename(fname))
            with open(download_path, "wb") as f:
                f.write(container_client.download_blob(fname).readall())
            if fname.endswith('.txt'):
                transcript = download_path
            elif fname.endswith('_agent.wav'):
                agent_audio = download_path
            elif fname.endswith('_emp.wav'):
                emp_audio = download_path
        if transcript and agent_audio and emp_audio:
            results.append({
                "call_id": call_id,
                "transcript": transcript,
                "agent_audio": agent_audio,
                "emp_audio": emp_audio
            })
    return results