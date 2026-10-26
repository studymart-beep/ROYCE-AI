"""Ad platform collectors: pull live campaign insights from Meta, TikTok and Google.

Each collector is a named connector with an access token and an account id.
Config is kept in collectors.json at the project root (git-ignored). Tokens are
never returned by the API; only configured/not-configured status is exposed.
"""
import json, os, datetime, urllib.request, urllib.parse, urllib.error

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


COLLECTORS = {c.name: c for c in (MetaCollector, TikTokCollector, GoogleCollector)}


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
        }
    return out


def fetch_all(preset="last_7d"):
    """Merge insights from every configured collector. Returns (rows, errors)."""
    rows, errors = [], []
    for name in COLLECTORS:
        c = get(name)
        if not c.configured:
            continue
        try:
            rows += c.fetch_insights(preset)
        except CollectorError as e:
            errors.append({"collector": name, "error": str(e)})
    return rows, errors


def configured_names():
    return [n for n in COLLECTORS if get(n).configured]
