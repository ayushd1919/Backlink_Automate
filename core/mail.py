# core/mail.py
import os, re, time, datetime as _dt, socket, ssl
from typing import Optional, List, Tuple

try:
    from imapclient import IMAPClient
except Exception:
    IMAPClient = None

class MailUnavailable(Exception):
    pass

def _env_true(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in {"1","true","yes","on"}

class MailClient:
    """
    Flexible mail fetcher with:
      - IMAP (default) with optional IPv4-only dialing
      - POP3 fallback (Gmail-friendly)
      - Multi-folder search (INBOX, All Mail, Spam, Important)
      - Debug logging: set MAIL_DEBUG=1
    ENV (used if args not provided):
      MAIL_PROTOCOL = imap | pop3
      MAIL_HOST, MAIL_PORT, MAIL_USER, MAIL_PASS, MAIL_SSL
      MAIL_FOLDER (imap), MAIL_FORCE_IPV4=1 (imap), MAIL_DEBUG=1
    """
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        ssl_enabled: Optional[bool] = None,
        folder: Optional[str] = None,
        protocol: Optional[str] = None,
    ):
        self.protocol = (protocol or os.getenv("MAIL_PROTOCOL") or "imap").strip().lower()
        self.host = host or os.getenv("MAIL_HOST")
        self.port = int(port or os.getenv("MAIL_PORT") or (995 if self.protocol=="pop3" else 993))
        self.user = user or os.getenv("MAIL_USER")
        self.password = password or os.getenv("MAIL_PASS")
        self.ssl_enabled = (ssl_enabled if ssl_enabled is not None else _env_true(os.getenv("MAIL_SSL","true")))
        self.folder = folder or os.getenv("MAIL_FOLDER") or "INBOX"
        self.force_ipv4 = _env_true(os.getenv("MAIL_FORCE_IPV4","0"))
        self.debug = _env_true(os.getenv("MAIL_DEBUG","0"))

    # ---------- public ----------
    def wait_for_verification_link(
        self,
        subject_hint: str = "",
        timeout_sec: int = 420,
        poll_interval_sec: int = 8,
        prefer_domain: Optional[str] = None,
    ) -> Optional[str]:
        """
        Try current protocol first; if IMAP fails, fall back to POP3 automatically.
        Returns link or None. Raises MailUnavailable only if there’s an immediate setup error.
        """
        deadline = time.time() + max(30, timeout_sec)
        subject_hint = (subject_hint or "").strip()
        prefer_domain = (prefer_domain or "").strip().lower()

        tried_pop = False
        while time.time() < deadline:
            try:
                if self.protocol == "imap":
                    link = self._poll_imap(subject_hint, prefer_domain, deadline, poll_interval_sec)
                    if link:
                        return link
                elif self.protocol == "pop3":
                    link = self._poll_pop3(subject_hint, prefer_domain, deadline, poll_interval_sec)
                    if link:
                        return link
                else:
                    raise MailUnavailable(f"Unknown MAIL_PROTOCOL: {self.protocol}")

            except MailUnavailable as e:
                # If IMAP unavailable, try POP3 once automatically
                if self.protocol == "imap" and not tried_pop:
                    if self.debug:
                        print(f"[MAIL] IMAP unavailable ({e}); switching to POP3 fallback…")
                    # Switch to POP3 defaults if not explicitly set
                    host = self.host or ""
                    # Heuristic defaults for Gmail/Outlook if unset
                    pop_host = self.host or ("pop.gmail.com" if "gmail" in host else "outlook.office365.com")
                    pop_port = int(os.getenv("MAIL_PORT") or 995)
                    self.protocol = "pop3"
                    self.host, self.port = pop_host, pop_port
                    tried_pop = True
                    continue
                else:
                    # Still unavailable
                    if self.debug:
                        print(f"[MAIL] MailUnavailable: {e}")
                    break

            # No link yet → wait a bit
            time.sleep(poll_interval_sec)

        if self.debug:
            print("[MAIL] Timed out without finding a verification link.")
        return None

    # ---------- IMAP ----------
    def _poll_imap(self, subject_hint: str, prefer_domain: str, deadline: float, poll_interval_sec: int) -> Optional[str]:
        if IMAPClient is None:
            raise MailUnavailable("imapclient is not installed.")

        since = _dt.date.today().strftime("%d-%b-%Y")
        folders_to_try = list(dict.fromkeys([self.folder, "INBOX", "[Gmail]/All Mail", "[Gmail]/Important", "[Gmail]/Spam"]))

        if self.debug:
            print(f"[MAIL/IMAP] host={self.host} port={self.port} ssl={self.ssl_enabled} user={self.user} ipv4_only={self.force_ipv4}")
            print(f"[MAIL/IMAP] folders={folders_to_try} subject_hint='{subject_hint}' prefer_domain='{prefer_domain}'")

        while time.time() < deadline:
            c = None
            try:
                c = self._imap_connect()
                for f in folders_to_try:
                    try:
                        c.select_folder(f, readonly=True)
                        if self.debug:
                            print(f"[MAIL/IMAP] SELECT {f}")
                    except Exception as e:
                        if self.debug:
                            print(f"[MAIL/IMAP] Cannot select {f}: {e}")
                        continue

                    # Phase 1: UNSEEN + SUBJECT
                    ids = self._imap_search(c, ["UNSEEN", "SUBJECT", subject_hint] if subject_hint else ["UNSEEN"], "UNSEEN+SUBJECT")
                    # Phase 2: UNSEEN + TEXT
                    if not ids:
                        ids = self._imap_search(c, ["UNSEEN","TEXT",subject_hint or "verify"], "UNSEEN+TEXT")
                    # Phase 3: SINCE today (seen or unseen)
                    if not ids:
                        if subject_hint:
                            ids = self._imap_search(c, ["SINCE", since, "SUBJECT", subject_hint], "SINCE+SUBJECT")
                        if not ids:
                            ids = self._imap_search(c, ["SINCE", since, "TEXT", subject_hint or "verify"], "SINCE+TEXT")

                    if not ids:
                        continue

                    ids_sorted = sorted(ids, reverse=True)
                    if self.debug:
                        print(f"[MAIL/IMAP] Candidates: {len(ids_sorted)}")

                    messages = c.fetch(ids_sorted, ["RFC822"])
                    for msgid in ids_sorted:
                        raw = messages.get(msgid, {}).get(b"RFC822", b"")
                        if not raw:
                            continue
                        text = self._decode_email(raw)
                        links = self._extract_links(text)
                        candidate = self._pick_best_link(links, prefer_domain or subject_hint)
                        if self.debug:
                            print(f"[MAIL/IMAP] msgid={msgid} links={len(links)} chosen={bool(candidate)}")
                        if candidate:
                            return candidate

                # Not found yet
                time.sleep(poll_interval_sec)

            except MailUnavailable:
                raise
            except Exception as e:
                if self.debug:
                    print(f"[MAIL/IMAP] transient error: {e}")
                time.sleep(poll_interval_sec)
            finally:
                try:
                    c and c.logout()
                except Exception:
                    pass

        return None

    def _imap_connect(self):
        if not all([self.host, self.port, self.user, self.password]):
            raise MailUnavailable("Missing MAIL_* settings (host/port/user/pass).")

        # Optional: prefer IPv4 to avoid IPv6/network quirks
        target_host = self.host
        if self.force_ipv4:
            try:
                infos = socket.getaddrinfo(self.host, self.port, family=socket.AF_INET, type=socket.SOCK_STREAM)
                # Choose the first IPv4 address
                if infos:
                    target_host = infos[0][4][0]
                    if self.debug:
                        print(f"[MAIL/IMAP] Forcing IPv4: {self.host} -> {target_host}")
            except Exception as e:
                if self.debug:
                    print(f"[MAIL/IMAP] IPv4 resolution failed ({e}); falling back to default host")

        try:
            c = IMAPClient(target_host, port=int(self.port), ssl=bool(self.ssl_enabled))
            c.login(self.user, self.password)
            return c
        except Exception as e:
            raise MailUnavailable(f"IMAP connect/login failed: {e}")

    def _imap_search(self, c: "IMAPClient", criteria, label=""):
        try:
            if self.debug:
                print(f"[MAIL/IMAP] SEARCH {label}: {criteria}")
            return c.search(criteria) or []
        except Exception as e:
            if self.debug:
                print(f"[MAIL/IMAP] SEARCH error ({label}): {e}")
            return []

    # ---------- POP3 fallback ----------
    def _poll_pop3(self, subject_hint: str, prefer_domain: str, deadline: float, poll_interval_sec: int) -> Optional[str]:
        """
        POP3 reads recent messages from the mailbox root (no folders).
        """
        import poplib, email
        if self.debug:
            print(f"[MAIL/POP3] host={self.host} port={self.port} ssl={self.ssl_enabled} user={self.user}")

        while time.time() < deadline:
            try:
                server = None
                if self.ssl_enabled:
                    server = poplib.POP3_SSL(self.host, self.port, timeout=15)
                else:
                    server = poplib.POP3(self.host, self.port, timeout=15)

                server.user(self.user)
                server.pass_(self.password)

                # Get message list (num, size). Read only last ~20
                (num_msgs, _total_bytes) = server.stat()
                start = max(1, num_msgs - 20 + 1)
                if self.debug:
                    print(f"[MAIL/POP3] Messages: {num_msgs}, scanning {start}..{num_msgs}")

                for i in range(num_msgs, start - 1, -1):
                    resp, lines, _octets = server.retr(i)
                    raw = b"\r\n".join(lines)
                    text = self._decode_email(raw)
                    # optional subject filter (best-effort)
                    if subject_hint and subject_hint.lower() not in text.lower():
                        # still scan for link, don't skip aggressively
                        pass

                    links = self._extract_links(text)
                    candidate = self._pick_best_link(links, prefer_domain or subject_hint)
                    if self.debug:
                        print(f"[MAIL/POP3] msg#{i} links={len(links)} chosen={bool(candidate)}")
                    if candidate:
                        try:
                            server.quit()
                        except Exception:
                            pass
                        return candidate

                try:
                    server.quit()
                except Exception:
                    pass

                time.sleep(poll_interval_sec)

            except Exception as e:
                if self.debug:
                    print(f"[MAIL/POP3] transient error: {e}")
                time.sleep(poll_interval_sec)

        return None

    # ---------- helpers ----------
    def _decode_email(self, raw_bytes: bytes) -> str:
        for enc in ("utf-8","latin-1"):
            try:
                return raw_bytes.decode(enc, errors="ignore")
            except Exception:
                continue
        return str(raw_bytes)

    def _extract_links(self, text: str) -> List[str]:
        url_re = re.compile(r"https?://[^\s<>\]\)\"']+", re.I)
        return url_re.findall(text or "")

    def _pick_best_link(self, links: List[str], hint: str) -> Optional[str]:
        if not links:
            return None
        hint = (hint or "").lower()
        domain = hint if "." in hint else None
        pool = links
        if domain:
            preferred = [u for u in links if domain in u.lower()]
            if preferred:
                pool = preferred

        def score(u: str) -> int:
            s = u.lower(); sc = 0
            if any(k in s for k in ("verify","verification","activate","confirm")): sc += 5
            if domain and domain in s: sc += 3
            if any(k in s for k in ("/signup","/register")): sc += 2
            return sc
        return sorted(pool, key=score, reverse=True)[0]
