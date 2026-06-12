# ============================================================
# config.py
# ============================================================
# Reads ALL credentials from environment variables.
# You never paste secrets into this file.
#
# HOW TO SET ENVIRONMENT VARIABLES:
#
# ── On Windows (Command Prompt) ──────────────────────────────
#   set ANGEL_API_KEY=your_api_key_here
#   set ANGEL_CLIENT_ID=A123456
#   set ANGEL_PASSWORD=1234
#   set ANGEL_TOTP_SECRET=your32charstring
#
# ── On Windows (PowerShell) ──────────────────────────────────
#   $env:ANGEL_API_KEY="your_api_key_here"
#   $env:ANGEL_CLIENT_ID="A123456"
#   $env:ANGEL_PASSWORD="1234"
#   $env:ANGEL_TOTP_SECRET="your32charstring"
#
# ── On Mac / Linux (Terminal) ────────────────────────────────
#   export ANGEL_API_KEY="your_api_key_here"
#   export ANGEL_CLIENT_ID="A123456"
#   export ANGEL_PASSWORD="1234"
#   export ANGEL_TOTP_SECRET="your32charstring"
#
# ── Using a .env file (recommended for daily use) ────────────
#   1. Create a file named  .env  in your project root
#   2. Add these lines to it (no quotes needed):
#
#      ANGEL_API_KEY=your_api_key_here
#      ANGEL_CLIENT_ID=A123456
#      ANGEL_PASSWORD=1234
#      ANGEL_TOTP_SECRET=your32charstring
#
#   3. Install python-dotenv:   pip install python-dotenv
#   4. The code below loads .env automatically if it exists
#   5. Add .env to your .gitignore — it is already listed there
#
# ── PERMANENT (so you don't re-type every terminal session) ──
#   Windows: Search "Environment Variables" in Start Menu
#            -> "Edit the system environment variables"
#            -> "Environment Variables" button
#            -> Add under "User variables"
#
#   Mac/Linux: Add the export lines to ~/.bashrc or ~/.zshrc
#              Then run: source ~/.bashrc
#
# ============================================================

import os

# Load .env file if it exists (requires: pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()   # looks for .env in current directory automatically
except ImportError:
    pass            # dotenv not installed — env vars must be set manually


def _require(var_name: str) -> str:
    """Read env var. Raise a clear error if it is not set."""
    value = os.environ.get(var_name, "").strip()
    if not value:
        raise EnvironmentError(
            f"\n\n  Missing environment variable: {var_name}\n"
            f"  Set it before running:\n"
            f"\n"
            f"  Windows CMD  : set {var_name}=your_value\n"
            f"  PowerShell   : $env:{var_name}=\"your_value\"\n"
            f"  Mac/Linux    : export {var_name}=\"your_value\"\n"
            f"  .env file    : add line  {var_name}=your_value\n"
        )
    return value


# These are called lazily — no error until the value is actually needed
def get_api_key()      -> str: return _require("ANGEL_API_KEY")
def get_client_id()    -> str: return _require("ANGEL_CLIENT_ID")
def get_password()     -> str: return _require("ANGEL_PASSWORD")
def get_totp_secret()  -> str: return _require("ANGEL_TOTP_SECRET")


# ── Quick validation — run this file directly to check your setup ────────────
if __name__ == "__main__":
    print("Checking environment variables...\n")
    all_ok = True
    for name, fn in [
        ("ANGEL_API_KEY",     get_api_key),
        ("ANGEL_CLIENT_ID",   get_client_id),
        ("ANGEL_PASSWORD",    get_password),
        ("ANGEL_TOTP_SECRET", get_totp_secret),
    ]:
        try:
            val = fn()
            # Show only first 4 chars for safety
            masked = val[:4] + "*" * (len(val) - 4) if len(val) > 4 else "****"
            print(f"  ✓  {name:<22} = {masked}")
        except EnvironmentError as e:
            print(f"  ✗  {name:<22} NOT SET")
            all_ok = False

    print()
    if all_ok:
        print("All credentials found. You are good to run main.py.")
    else:
        print("Set the missing variables above, then run this check again.")