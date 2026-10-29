from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def upload_video(video_path, title, description, privacy_status,
                 client_file: Path, token_file: Path, tags=None, publish_at=None):
    if not client_file.exists():
        raise FileNotFoundError(
            f"Missing {client_file.name}. Create a Desktop OAuth client in Google Cloud "
            "and place the downloaded JSON beside main.py."
        )

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "20",
            "tags": tags or ["Valorant", "Valorant Shorts", "Gaming Shorts", "Shorts"],
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    if publish_at:
        if privacy_status != "private":
            raise ValueError("Scheduled publishing requires privacy_status=private at upload time.")
        body["status"]["publishAt"] = publish_at

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=8 * 1024 * 1024,
            resumable=True,
        ),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]
