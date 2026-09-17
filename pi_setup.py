#!/usr/bin/env python3
"""Generate tokens.json for headless Raspberry Pi setup.

Run this once on your PC (or Pi with display) to authenticate with Schwab.
Copy the resulting tokens.json to your Pi.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_bot import SchwabAPI, Config, log

def main():
    api = SchwabAPI()
    
    # Check if already authenticated
    if api._is_authenticated():
        print("Token still valid. No need to re-authenticate.")
        print(f"Token expires: {api.token_expiry}")
        return
    
    # Try refresh first
    if api.refresh_token and api.access_token:
        try:
            api._refresh_tokens()
            print("Token refreshed successfully!")
            print(f"New token expires: {api.token_expiry}")
            return
        except Exception as e:
            print(f"Refresh failed: {e}")
            print("Re-authenticating...")
    
    # Manual auth flow (no browser)
    auth_url = (
        f"{api.base_url}/oauth/authorize"
        f"?client_id={Config.APP_KEY}"
        f"&redirect_uri={Config.REDIRECT_URI}"
        f"&response_type=code"
    )
    
    print("\n" + "=" * 60)
    print("SCHWAB AUTHENTICATION - Headless Mode")
    print("=" * 60)
    print(f"\n1. Open this URL in a browser on ANY device:\n\n{auth_url}\n")
    print("2. Log in to Schwab and authorize the app")
    print("3. You'll be redirected to a local URL (may not load - that's OK)")
    print("4. Copy the 'code' parameter from the redirect URL")
    print("   (or paste the entire redirect URL below)")
    print("=" * 60 + "\n")
    
    code = input("Enter authorization code or redirect URL: ").strip()
    
    # Extract code from URL if needed
    import urllib.parse
    if 'code=' in code:
        if code.startswith('http'):
            query = urllib.parse.urlparse(code).query
        else:
            query = code.split('?')[-1] if '?' in code else code
        params = urllib.parse.parse_qs(query)
        code = params.get('code', [''])[0]
    else:
        code = code.split('&')[0]
    
    try:
        api._exchange_code(code)
        print("\nAuthentication successful!")
        print(f"Access token expires: {api.token_expiry}")
        print(f"Token saved to: {Config.TOKEN_PATH}")
        
        # Verify by fetching account
        acct_hash = api.get_account_hash()
        if acct_hash:
            print(f"Account verified: {acct_hash[:20]}...")
        else:
            print("Warning: Could not verify account. Token may be invalid.")
    except Exception as e:
        print(f"\nAuthentication failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
