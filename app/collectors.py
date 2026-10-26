"""Ad platform collectors: pull live campaign insights from Meta, TikTok and Google.

Each collector is a named connector with an access token and an account id.
Config is kept in collectors.json at the project root (git-ignored). Tokens are
never returned by the API; only configured/not-configured status is exposed.
"""
import json, os, datetime, imaplib, email, email.utils, email.header, urllib.request, urllib.parse, urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE, "collectors.json")
NORMAL_FIELDS = ("platform", "campaign", "spend", "impressions", "clicks", "ctr", "cpc")


class CollectorError(Exception):
    pass


def _load():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save(d):
    with open(CONFIG_PATH, "w") as f:
        json.dump(d, f, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def _http(method, url, headers=None, timeout=25):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise CollectorError(f"HTTP {e.code} from platform: {body}")
    except urllib.error.URLError as e:
        raise CollectorError(f"network error reaching platform: {e.reason}")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _date_range(preset):
    """Map a preset to (start, end) ISO dates for TikTok (which takes explicit dates)."""
    today = datetime.date.today()
    end = today - datetime.timedelta(days=1)  # yesterday: today is incomplete
    if preset == "today":
        return today.isoformat(), today.isoformat()
    if preset == "yesterday":
        return end.isoformat(), end.isoformat()
    if preset == "this_month":
        return today.replace(day=1).isoformat(), end.isoformat()
    days = {"last_7d": 7, "last_14d": 14, "last_30d": 30, "last_90d": 90}.get(preset, 7)
    return (end - datetime.timedelta(days=days - 1)).isoformat(), end.isoformat()


class Collector:
    name = "base"
    label = "base"
    token_help = ""
    account_label = "account id"
    token_label = "access token"
    is_ads = True  # ad platforms feed campaign reports; inbox and social connectors do not

    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    @property
    def configured(self):
        return bool(self.cfg.get("token") and self.cfg.get("account_id"))

    def configure(self, token, account_id):
        token = (token or "").strip()
        account_id = str(account_id or "").strip()
        if not token or not account_id:
            raise CollectorError(f"{self.label}: token and {self.account_label} are both required")
        self.cfg = {"token": token, "account_id": account_id}
        d = _load()
        d[self.name] = self.cfg
        _save(d)

    def test(self):
        raise NotImplementedError

    def fetch_insights(self, preset="last_7d"):
        raise NotImplementedError


class MetaCollector(Collector):
    name = "meta"
    label = "Meta Ads"
    account_label = "ad account id (numbers only, without act_)"
    token_help = "System user token from Meta Business Settings > Users > System users > Generate token (ads_read permission)"
    API = "https://graph.facebook.com/v21.0"

    def _acct(self):
        if not self.configured:
            raise CollectorError("Meta Ads: not connected yet (paste a system user token and ad account id)")
        return str(self.cfg["account_id"]).replace("act_", "")

    def _headers(self):
        self._acct()
        return {"Authorization": "Bearer " + self.cfg["token"]}

    def test(self):
        acct = self._acct()
        url = f"{self.API}/act_{acct}?fields=name,account_status"
        data = _http("GET", url, self._headers())
        return {"ok": True, "account_name": data.get("name", ""), "account_status": data.get("account_status")}

    def fetch_insights(self, preset="last_7d"):
        acct = self._acct()
        params = urllib.parse.urlencode({
            "fields": "campaign_name,spend,impressions,clicks,ctr,cpc",
            "date_preset": preset,
            "level": "campaign",
            "limit": 500,
        })
        url = f"{self.API}/act_{acct}/insights?{params}"
        rows = []
        while url:
            data = _http("GET", url, self._headers())
            for r in data.get("data", []):
                rows.append({
                    "platform": "meta",
                    "campaign": r.get("campaign_name", ""),
                    "spend": round(_num(r.get("spend")), 2),
                    "impressions": int(_num(r.get("impressions"))),
                    "clicks": int(_num(r.get("clicks"))),
                    "ctr": round(_num(r.get("ctr")), 2),
                    "cpc": round(_num(r.get("cpc")), 2),
                })
            url = (data.get("paging") or {}).get("next")
        return rows


class TikTokCollector(Collector):
    name = "tiktok"
    label = "TikTok Ads"
    account_label = "advertiser id"
    token_help = "Access token from your TikTok for Business developer app (Marketing API, Reporting read scope)"
    API = "https://business-api.tiktok.com/open_api/v1.3"

    def _headers(self):
        if not self.configured:
            raise CollectorError("TikTok Ads: not connected yet (paste a business api token and advertiser id)")
        return {"Access-Token": self.cfg["token"], "Content-Type": "application/json"}

    def test(self):
        q = urllib.parse.urlencode({"advertiser_ids": json.dumps([str(self.cfg["account_id"])])})
        data = _http("GET", f"{self.API}/advertiser/info/?{q}", self._headers())
        if data.get("code") not in (0, None):
            raise CollectorError(f"TikTok: {data.get('message', 'token check failed')} (code {data.get('code')})")
        items = (data.get("data") or {}).get("list", [])
        name = items[0].get("name", "") if items else ""
        return {"ok": True, "account_name": name}

    def fetch_insights(self, preset="last_7d"):
        start, end = _date_range(preset)
        params = {
            "advertiser_id": str(self.cfg["account_id"]),
            "report_type": "BASIC",
            "data_level": "AUCTION_CAMPAIGN",
            "dimensions": json.dumps(["campaign_id"]),
            "metrics": json.dumps(["campaign_name", "spend", "impressions", "clicks", "ctr", "cpc"]),
            "start_date": start,
            "end_date": end,
            "page_size": 200,
        }
        url = f"{self.API}/report/integrated/get/?{urllib.parse.urlencode(params)}"
        data = _http("GET", url, self._headers())
        if data.get("code") not in (0, None):
            raise CollectorError(f"TikTok: {data.get('message', 'report failed')} (code {data.get('code')})")
        rows = []
        for r in (data.get("data") or {}).get("list", []):
            m = r.get("metrics", {})
            rows.append({
                "platform": "tiktok",
                "campaign": m.get("campaign_name", r.get("dimensions", {}).get("campaign_id", "")),
                "spend": round(_num(m.get("spend")), 2),
                "impressions": int(_num(m.get("impressions"))),
                "clicks": int(_num(m.get("clicks"))),
                "ctr": round(_num(m.get("ctr")), 2),
                "cpc": round(_num(m.get("cpc")), 2),
            })
        return rows


class GmailCollector(Collector):
    """Gmail over IMAP with a Google app password (2-Step Verification must be on).
    Read-only: counts recent mail and lists unread senders. Sending is not enabled here."""
    name = "gmail"
    label = "Gmail"
    is_ads = False
    account_label = "gmail address"
    token_label = "app password (16 characters)"
    token_help = "Google Account > Security > 2-Step Verification > App passwords. Create one called Royce AI and paste the 16 characters here, plus your gmail address."
    HOST = "imap.gmail.com"

    def _login(self):
        if not self.configured:
            raise CollectorError("Gmail: not connected yet (paste an app password and your gmail address)")
        try:
            m = imaplib.IMAP4_SSL(self.HOST, timeout=25)
            m.login(self.cfg["account_id"], self.cfg["token"].replace(" ", ""))
            return m
        except imaplib.IMAP4.error as e:
            raise CollectorError(f"Gmail login failed: {e}. Check the address and use an app password, not your normal password.")
        except OSError as e:
            raise CollectorError(f"network error reaching Gmail: {e}")

    def test(self):
        m = self._login()
        try:
            m.select("INBOX", readonly=True)
            typ, data = m.search(None, "UNSEEN")
            unread = len(data[0].split()) if typ == "OK" and data and data[0] else 0
        finally:
            try: m.logout()
            except Exception: pass
        return {"ok": True, "account_name": self.cfg["account_id"], "unread": unread}

    def summary(self, days=7, top=10):
        """Recent message count, unread count and the most frequent unread senders."""
        m = self._login()
        try:
            m.select("INBOX", readonly=True)
            since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = m.search(None, "SINCE", since)
            recent = len(data[0].split()) if typ == "OK" and data and data[0] else 0
            typ, data = m.search(None, "UNSEEN")
            unread_ids = data[0].split() if typ == "OK" and data and data[0] else []
            senders = {}
            for mid in unread_ids[-200:]:
                typ, msg = m.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
                if typ != "OK" or not msg or not isinstance(msg[0], tuple):
                    continue
                raw = msg[0][1].decode("utf-8", "replace")
                frm = raw.split(":", 1)[1].strip() if ":" in raw else raw.strip()
                name, addr = email.utils.parseaddr(frm)
                key = (name or addr or frm).strip()
                try:
                    key = str(email.header.make_header(email.header.decode_header(key)))
                except Exception:
                    pass
                senders[key] = senders.get(key, 0) + 1
        finally:
            try: m.logout()
            except Exception: pass
        ranked = sorted(senders.items(), key=lambda kv: -kv[1])[:top]
        return {"days": days, "recent": recent, "unread": len(unread_ids),
                "unread_senders": [{"sender": k, "count": v} for k, v in ranked]}

    def fetch_insights(self, preset="last_7d"):
        raise CollectorError("Gmail is an inbox connector, not an ad platform. Ask for an inbox summary instead.")


class SocialStubCollector(Collector):
    """Facebook, X, Instagram, LinkedIn: the token is stored so the connection shows in the UI,
    but live insights are not built yet. Nothing here returns fake numbers."""
    is_ads = False
    account_label = "account or page id (optional)"
    token_label = "access token"
    coming = "coming soon"

    @property
    def configured(self):
        return bool(self.cfg.get("token"))

    def configure(self, token, account_id=""):
        token = (token or "").strip()
        if not token:
            raise CollectorError(f"{self.label}: an access token is required")
        self.cfg = {"token": token, "account_id": str(account_id or "").strip()}
        d = _load()
        d[self.name] = self.cfg
        _save(d)

    def test(self):
        raise CollectorError(f"{self.label}: token saved. live insights are {self.coming}: this connector does not pull data yet.")

    def fetch_insights(self, preset="last_7d"):
        raise CollectorError(f"{self.label}: live insights are {self.coming}. Token saved, no data pulled yet.")


class FacebookCollector(SocialStubCollector):
    name = "facebook"
    label = "Facebook"
    token_help = "Facebook Page or Meta Graph access token with pages_read_engagement. Insights coming soon."


class XCollector(SocialStubCollector):
    name = "x"
    label = "X (Twitter)"
    token_help = "X API bearer token (developer portal, read access). Insights coming soon."


class InstagramCollector(SocialStubCollector):
    name = "instagram"
    label = "Instagram"
    token_help = "Instagram Graph API access token for your business account (insights scope). Insights coming soon."


class LinkedInCollector(SocialStubCollector):
    name = "linkedin"
    label = "LinkedIn"
    token_help = "LinkedIn API access token with r_organization_social read scope. Insights coming soon."


class GoogleCollector(Collector):
    """Google Ads needs an OAuth client, a user refresh token and a developer token.
    Not implemented yet: this stub refuses clearly instead of returning fake data."""
    name = "google"
    label = "Google Ads"
    account_label = "customer id"
    token_help = "Google Ads needs OAuth (client id/secret + refresh token) and a developer token. Coming soon."

    def test(self):
        raise CollectorError("Google Ads: connect via OAuth first (OAuth refresh token + developer token are required). Not connected yet.")

    def fetch_insights(self, preset="last_7d"):
        raise CollectorError("Google Ads: connect via OAuth first (OAuth refresh token + developer token are required). Not connected yet.")

    def configure(self, token, account_id):
        raise CollectorError("Google Ads: connect via OAuth first (OAuth refresh token + developer token are required). Not connected yet.")


COLLECTORS = {c.name: c for c in (MetaCollector, TikTokCollector, GoogleCollector,
                                  GmailCollector, FacebookCollector, XCollector, InstagramCollector, LinkedInCollector)}
AD_NAMES = [n for n, c in COLLECTORS.items() if c.is_ads]


def get(name):
    if name not in COLLECTORS:
        raise CollectorError(f"unknown collector '{name}'")
    cfg = _load().get(name, {})
    return COLLECTORS[name](cfg)


def status():
    out = {}
    stored = _load()
    for name, cls in COLLECTORS.items():
        c = cls(stored.get(name, {}))
        out[name] = {
            "label": c.label,
            "configured": c.configured,
            "account_label": c.account_label,
            "token_help": c.token_help,
            "account_id": c.cfg.get("account_id", "") if c.configured else "",
            "token_label": c.token_label,
            "is_ads": c.is_ads,
        }
    return out


def fetch_all(preset="last_7d"):
    """Merge insights from every configured collector. Returns (rows, errors)."""
    rows, errors = [], []
    for name in AD_NAMES:
        c = get(name)
        if not c.configured:
            continue
        try:
            rows += c.fetch_insights(preset)
        except CollectorError as e:
            errors.append({"collector": name, "error": str(e)})
    return rows, errors


def configured_names():
    """Ad platforms with a connection (used for campaign reports)."""
    return [n for n in AD_NAMES if get(n).configured]


def configured_inboxes():
    return [n for n, c in COLLECTORS.items() if not c.is_ads and get(n).configured]
