
import os
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

def sync_all_calls_from_cloud(service_account_json, folder_id, download_dir):
    """
    Syncs all call sets (transcript + agent + emp audio) from a Google Drive folder.
    Downloads files to download_dir and returns a list of dicts for each call set.
    """
    # Authenticate with Google Drive
    creds = service_account.Credentials.from_service_account_file(service_account_json, scopes=["https://www.googleapis.com/auth/drive"])
    service = build('drive', 'v3', credentials=creds)

    # List all files in the folder
    query = f"'{folder_id}' in parents and trashed = false"
    files = []
    page_token = None
    while True:
        response = service.files().list(q=query, fields="nextPageToken, files(id, name)", pageToken=page_token).execute()
        files.extend(response.get('files', []))
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break

    # Group files by call_id
    calls = {}
    for f in files:
        name = f['name']
        if name.endswith('.txt'):
            base = name.split('.')[0]
            calls.setdefault(base, {})['transcript'] = f
        elif name.endswith('_agent.wav'):
            base = name.replace('_agent.wav', '')
            calls.setdefault(base, {})['agent_audio'] = f
        elif name.endswith('_emp.wav'):
            base = name.replace('_emp.wav', '')
            calls.setdefault(base, {})['emp_audio'] = f

    results = []
    for call_id, file_set in calls.items():
        if 'transcript' in file_set and 'agent_audio' in file_set and 'emp_audio' in file_set:
            downloaded = {}
            for key in ['transcript', 'agent_audio', 'emp_audio']:
                file_info = file_set[key]
                file_id = file_info['id']
                file_name = file_info['name']
                file_path = os.path.join(download_dir, file_name)
                request = service.files().get_media(fileId=file_id)
                fh = io.FileIO(file_path, 'wb')
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                downloaded[key] = file_path
            results.append({'call_id': call_id, **downloaded})
    return results
