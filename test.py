import os, datetime as dt
from imapclient import IMAPClient
host = os.getenv("MAIL_HOST"); port = int(os.getenv("MAIL_PORT","993"))
user = os.getenv("MAIL_USER"); pw = os.getenv("MAIL_PASS")
print("Connecting to", host, port, "as", user)
c = IMAPClient(host, port=port, ssl=True)
c.login(user, pw)
for f in ["INBOX","[Gmail]/All Mail","[Gmail]/Spam","[Gmail]/Important"]:
    try:
        c.select_folder(f, readonly=True)
        ids = c.search(["SINCE", dt.date.today().strftime("%d-%b-%Y")])
        print(f, "OK. messages today:", len(ids))
    except Exception as e:
        print(f, "ERR:", e)
c.logout()