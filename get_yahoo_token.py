"""
One-time OAuth setup for Yahoo Fantasy leagues. Run this once PER YAHOO
ACCOUNT (not per league - one Yahoo account's refresh_token covers
every league you're in on that account) to get the refresh_token you'll
put in .env.

Before running this, register an app at https://developer.yahoo.com/apps/
(Create App -> give it any name -> under "API Permissions" check
"Fantasy Sports" with Read access -> Redirect URI(s): use
https://localhost:8080 or leave the default, it doesn't matter since
we use Yahoo's out-of-band flow below -> Create). Grab the Client ID
and Client Secret from the app's page - see the README for the full
walkthrough with screenshots-equivalent detail.

Usage:
    python get_yahoo_token.py
"""

import base64
import webbrowser

import requests

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"


def main():
    print("Paste the Client ID and Client Secret from your Yahoo app")
    print("(developer.yahoo.com/apps/ -> your app -> the API keys shown there).\n")
    client_id = input("Client ID: ").strip()
    client_secret = input("Client Secret: ").strip()

    # "oob" (out-of-band) redirect - Yahoo shows the authorization code
    # directly on a web page for you to copy/paste back here, instead of
    # needing a local callback server running.
    auth_url = f"{AUTH_URL}?client_id={client_id}&redirect_uri=oob&response_type=code&language=en-us"

    print(f"\nOpening your browser to authorize this app:\n  {auth_url}\n")
    print("(If it doesn't open automatically, paste that URL into a browser yourself.)")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    code = input("\nLog in and approve access, then paste the code Yahoo shows you here: ").strip()

    creds = f"{client_id}:{client_secret}"
    basic = base64.b64encode(creds.encode()).decode()

    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "authorization_code", "redirect_uri": "oob", "code": code},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    print("\nSuccess! Add these three lines to your .env file:\n")
    print(f"YAHOO_CLIENT_ID={client_id}")
    print(f"YAHOO_CLIENT_SECRET={client_secret}")
    print(f"YAHOO_REFRESH_TOKEN={data['refresh_token']}")
    print("\nThe access_token itself is short-lived and isn't needed here -")
    print("yahoo_client.py uses the refresh_token to mint new access tokens")
    print("automatically on every run, no more browser steps after this.")


if __name__ == "__main__":
    main()