from Cloud_sync.cloudsync import authenticate_google_drive, list_files_in_folder, download_file
import os

def sync_call_from_cloud(folder_id, call_id, download_dir):
    """
    Downloads transcript and audio files for a given call_id from Google Drive.
    Returns a dict with file paths for transcript, agent audio, and employee audio.
    """
    service = authenticate_google_drive()
    files = list_files_in_folder(service, folder_id)
    needed = [f"{call_id}.txt", f"{call_id}_agent.wav", f"{call_id}_emp.wav"]
    downloaded = {}
    for file in files:
        if file['name'] in needed:
            path = download_file(service, file['id'], file['name'], download_dir)
            downloaded[file['name']] = path
    # Return dict with keys: transcript, agent_audio, emp_audio
    return {
        'transcript': downloaded.get(f"{call_id}.txt"),
        'agent_audio': downloaded.get(f"{call_id}_agent.wav"),
        'emp_audio': downloaded.get(f"{call_id}_emp.wav")
    }

def sync_latest_call_from_cloud(folder_id, download_dir):
    """
    Downloads the latest transcript and audio files from a given folder_id in Google Drive.
    Assumes the latest call is the one with the highest numeric value in the transcript filename.
    Returns a dict with file paths for transcript, agent audio, and employee audio, and the base name of the latest call.
    """
    service = authenticate_google_drive()
    files = list_files_in_folder(service, folder_id)
    # Filter for .txt files (transcripts) and sort by name (assuming latest has highest number)
    transcript_files = [f for f in files if f['name'].endswith('.txt')]
    if not transcript_files:
        return None
    # Sort by numeric part of filename (e.g., 271103.txt)
    transcript_files.sort(key=lambda x: int(x['name'].split('.')[0]), reverse=True)
    latest_base = transcript_files[0]['name'].split('.')[0]
    needed = [f"{latest_base}.txt", f"{latest_base}_agent.wav", f"{latest_base}_emp.wav"]
    downloaded = {}
    for file in files:
        if file['name'] in needed:
            path = download_file(service, file['id'], file['name'], download_dir)
            downloaded[file['name']] = path
    return downloaded, latest_base
