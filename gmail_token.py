"""One-time helper to obtain a Gmail OAuth token for use as a Modal secret.

Run this locally (you only need it once):

    pip install google-auth-oauthlib google-auth
    python gmail_token.py

You must first create OAuth credentials in Google Cloud Console:

1. Go to https://console.cloud.google.com/apis/credentials
2. Create an "OAuth client ID" of type "Desktop app".
3. Download the client config as ``credentials.json`` into this directory.
4. Make sure the Gmail API is enabled:
   https://console.cloud.google.com/apis/library/gmail.googleapis.com

The script opens a browser, you authorize access, and it prints a JSON token
containing a refresh_token. Paste that JSON (as a single line) into your Modal
secret under the key ``GMAIL_TOKEN_JSON``.
"""

import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(
        open_browser=False,
        port=8080,
        prompt="consent",
    )

    token_json = json.dumps(json.loads(creds.to_json()))
    with open("token.json", "w") as f:
        f.write(token_json)

    print("\nSaved to token.json. Copy the JSON below into your Modal secret as GMAIL_TOKEN_JSON:\n")
    print(token_json)


if __name__ == "__main__":
    main()
