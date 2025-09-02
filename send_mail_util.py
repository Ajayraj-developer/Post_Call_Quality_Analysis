import requests

def send_email(to_address, subject, body):
    tenant_id = 'f5791d91-daca-4d28-8700-680f7a2f8b6a'
    client_id = '7538b9b8-e0b3-4b86-8731-892c9cd79266'
    client_secret = 'fao8Q~rPgEr8..4AlnTy4YGN1g09whA2~b9Dhc4p'

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    token_data = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
        'scope': 'https://graph.microsoft.com/.default'
    }
    token_r = requests.post(token_url, data=token_data)
    token = token_r.json().get('access_token')
    if not token:
        print("Failed to obtain access token.")
        return False

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    email_data = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "text",
                "content": body
            },
            "toRecipients": [{"emailAddress": {"address": to_address}}]
        },
        "saveToSentItems": True
    }
    user_email = "Admin@lwpcoe.onmicrosoft.com"
    response = requests.post(
        f'https://graph.microsoft.com/v1.0/users/{user_email}/sendMail',
        headers=headers,
        json=email_data
    )
    return response.status_code == 202
