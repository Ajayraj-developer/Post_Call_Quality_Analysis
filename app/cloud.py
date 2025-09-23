
import os
import io

# Step 1: Add Azure Blob Storage sync function
from azure.storage.blob import BlobServiceClient
import os

def sync_all_calls_from_azure(storage_account_name, container_name, sas_token, download_dir):
    blob_service_client = BlobServiceClient(
        account_url=f"https://{storage_account_name}.blob.core.windows.net",
        credential=sas_token
    )
    container_client = blob_service_client.get_container_client(container_name)
    os.makedirs(download_dir, exist_ok=True)

    call_files = {}
    for blob in container_client.list_blobs():
        base_name = blob.name.split('.')[0].replace('_agent', '').replace('_emp', '')
        call_files.setdefault(base_name, []).append(blob.name)

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
