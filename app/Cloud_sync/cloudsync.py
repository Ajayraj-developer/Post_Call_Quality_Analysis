import os
import io
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

# If modifying these scopes, delete the file token.json.
# 'https://www.googleapis.com/auth/drive.readonly' for read-only access
# 'https://www.googleapis.com/auth/drive' for full read/write access
SCOPES = ['https://www.googleapis.com/auth/drive']

def authenticate_google_drive():
    """Authenticates with Google Drive API and returns the service object.
    The file token.json stores the user's access and refresh tokens, and is
    created automatically when the authorization flow completes for the first
    time.
    """
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=8080)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

def list_files_in_folder(service, folder_id):
    """
    Lists all files and folders within a specific Google Drive folder.
    Args:
        service: The authenticated Google Drive service object.
        folder_id: The ID of the Google Drive folder to list contents from.
    Returns:
        A list of dictionaries, each representing a file/folder with 'id' and 'name'.
    """
    try:
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageSize=1000  # Adjust as needed for larger folders
        ).execute()
        items = results.get('files', [])

        if not items:
            print(f'No files found in folder ID: {folder_id}.')
            return []
        else:
            print(f'Files and folders in folder ID {folder_id}:')
            for item in items:
                print(f"  {item['name']} (ID: {item['id']}, Type: {item['mimeType']})")
            return items
    except HttpError as error:
        print(f'An error occurred: {error}')
        return []

def download_file(service, file_id, file_name, destination_path='.'):
    """
    Downloads a file from Google Drive.
    Args:
        service: The authenticated Google Drive service object.
        file_id: The ID of the file to download.
        file_name: The name to save the downloaded file as.
        destination_path: The directory where the file will be saved.
    Returns:
        The path to the downloaded file, or None if an error occurred.
    """
    try:
        # Check if it's a Google Doc/Sheet/Slide (Google Workspace native format)
        # These need to be exported in a specific format
        file_metadata = service.files().get(fileId=file_id, fields='mimeType, name').execute()
        mime_type = file_metadata.get('mimeType')

        if mime_type.startswith('application/vnd.google-apps.'):
            # Export Google Docs as plain text, PDFs, or other suitable formats
            # For a transcript, text or PDF is usually best
            if mime_type == 'application/vnd.google-apps.document':
                print(f"Downloading Google Doc '{file_name}' as text.")
                request = service.files().export_media(fileId=file_id, mimeType='text/plain')
                actual_file_name = f"{os.path.splitext(file_name)[0]}.txt" # Ensure .txt extension
            elif mime_type == 'application/vnd.google-apps.spreadsheet':
                print(f"Downloading Google Sheet '{file_name}' as CSV.")
                request = service.files().export_media(fileId=file_id, mimeType='text/csv')
                actual_file_name = f"{os.path.splitext(file_name)[0]}.csv" # Ensure .csv extension
            else:
                print(f"Unsupported Google Workspace file type for download: {mime_type}. Skipping {file_name}.")
                return None
        else:
            # Regular file types (PDF, MP3, TXT, etc.) can be downloaded directly
            print(f"Downloading regular file '{file_name}'.")
            request = service.files().get_media(fileId=file_id)
            actual_file_name = file_name # Keep original file name

        file_path = os.path.join(destination_path, actual_file_name)
        fh = io.FileIO(file_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            print(f"Download progress for '{actual_file_name}': {int(status.progress() * 100)}%.")
        print(f"Downloaded '{actual_file_name}' to '{file_path}'.")
        return file_path
    except HttpError as error:
        print(f'An error occurred during download: {error}')
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None

if __name__ == '__main__':
    # --- Configuration ---
    # Replace with the actual ID of your Google Drive folder
    # You can get this from the URL when you open the folder in Google Drive.
    # E.g., if the URL is https://drive.google.com/drive/folders/YOUR_FOLDER_ID_HERE
    # The folder ID is 'YOUR_FOLDER_ID_HERE'
    TARGET_FOLDER_ID = '1F5JG1JCG94Pkuj5CPsevbk1x_BqWg5vJ' # <--- IMPORTANT: Change this!
    DOWNLOAD_DIRECTORY = 'downloaded_transcripts' # Directory to save files to

    if not os.path.exists(DOWNLOAD_DIRECTORY):
        os.makedirs(DOWNLOAD_DIRECTORY)

    # --- Main execution ---
    service = authenticate_google_drive()

    if service:
        # 1. List files in the target folder
        files_in_folder = list_files_in_folder(service, TARGET_FOLDER_ID)

        # 2. Iterate and download relevant files (e.g., transcripts)
        if files_in_folder:
            for file_info in files_in_folder:
                file_id = file_info['id']
                file_name = file_info['name']
                mime_type = file_info['mimeType']

                # Example: Only download text-based files or Google Docs for transcripts
                if 'text/plain' in mime_type or 'application/pdf' in mime_type or \
                   'application/vnd.google-apps.document' in mime_type:
                    print(f"\nAttempting to download: {file_name}")
                    download_file(service, file_id, file_name, DOWNLOAD_DIRECTORY)
                else:
                    print(f"\nSkipping file '{file_name}' (Type: {mime_type}) - not a desired transcript format.")

        print("\n--- Process Completed ---")
    else:
        print("Failed to authenticate with Google Drive.")
