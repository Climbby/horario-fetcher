import argparse
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

from ucshared import academic_year, normalize_semester, slugify

try:
    from dotenv import load_dotenv
except ImportError:
    # A local .env is a convenience for running this by hand. On Actions the
    # variables arrive as secrets, so the dependency must not be required.
    def load_dotenv(*_args, **_kwargs):
        return False

# The service key grants full write access, so it is read from that .env and
# never passed on the command line, where it would land in shell history and
# in the process list.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# apps.uc.pt is the public course catalogue: no login, no JavaScript, no
# Selenium. It knows every course and the curricular year of every subject,
# which is exactly what the authenticated enrolment pages never expose.
BASE = "https://apps.uc.pt/courses/PT"

USER_AGENT = (
    "HorarioPicker-catalog-crawler/1.0 "
    "(+https://github.com/Climbby/horario-fetcher)"
)

# The search form posts one "type" per degree kind. Doutoramentos and
# non-degree courses are left out on purpose: they rarely have fixed shifts.
DEGREE_TYPES = [
    "PRIMEIRO",
    "PRIMEIRO_SEGUNDO",
    "SEGUNDO_CONTINUIDADE",
    "SEGUNDO_ESPECIALIZACAO_AVANCADA",
    "SEGUNDO_FORMACAO_LONGO_VIDA",
]

REQUEST_INTERVAL = 0.5  # seconds between requests: two per second, no more.
CACHE_FILE = "catalog_cache.json"

# --- HTTP -------------------------------------------------------------------

class Fetcher:
    """Rate-limited GET with retries. Politeness is not optional here."""

    def __init__(self, interval=REQUEST_INTERVAL):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.interval = interval
        self.last_request = 0.0
        self.count = 0

    def get(self, url, params=None, attempts=3):
        for attempt in range(1, attempts + 1):
            wait = self.interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            self.count += 1
            try:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response.text
            except requests.RequestException as e:
                if attempt == attempts:
                    raise
                backoff = 2 ** attempt
                print(f"    aviso: {e} - nova tentativa em {backoff}s")
                time.sleep(backoff)

# --- PARSING ----------------------------------------------------------------

def parse_index(html):
    """Course cards from a search result page."""
    soup = BeautifulSoup(html, 'html.parser')
    courses = []
    for card in soup.select('.course-modern-card'):
        link = card.select_one('a[href*="/course/"]')
        if not link:
            continue
        match = re.search(r'/course/(\d+)', link.get('href', ''))
        if not match:
            continue

        def text_of(selector):
            el = card.select_one(selector)
            return el.get_text(' ', strip=True) if el else ""

        courses.append({
            "uc_course_id": int(match.group(1)),
            "name": text_of('.course-designacao'),
            "acronym": text_of('.course-sigla'),
            "degree_type": text_of('.course-type-badge'),
            "faculty": text_of('.course-ou-badge'),
        })
    return courses

def parse_course(html):
    """The branches (ramos) of one course. A course always has at least one."""
    soup = BeautifulSoup(html, 'html.parser')
    heading = soup.select_one('h1')
    branches = {}
    for link in soup.select('a[href*="/programme/"]'):
        match = re.search(r'id_branch=(\d+)', link.get('href', ''))
        if not match:
            continue
        branch_id = int(match.group(1))
        # The same branch is linked more than once; the first label wins.
        branches.setdefault(branch_id, link.get_text(' ', strip=True))
    return {
        "name": heading.get_text(' ', strip=True) if heading else "",
        "branches": [
            {"uc_branch_id": bid, "name": name} for bid, name in branches.items()
        ],
    }

def parse_programme(html):
    """The study plan: one flat table whose 'Ano' column carries the year."""
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('table.sp-table') or soup.select_one('table')
    if not table:
        return []

    units = []
    for row in table.select('tr'):
        cells = row.select('td')
        if len(cells) < 6:
            continue  # header rows repeat once per year block

        values = [c.get_text(' ', strip=True) for c in cells[:6]]
        name, year, regime, kind, area, ects = values

        link = row.select_one('a[href*="/unit/"]')
        unit_match = re.search(r'/unit/(\d+)', link.get('href', '')) if link else None

        units.append({
            "uc_unit_id": int(unit_match.group(1)) if unit_match else None,
            "name": name,
            "curricular_year": int(year) if year.isdigit() else None,
            "semester": normalize_semester(regime),
            "is_optional": 'obrigat' not in kind.lower(),
            "area": area,
            "ects": float(ects.replace(',', '.')) if re.match(r'^[\d.,]+$', ects) else None,
        })
    return units

def parse_unit(html):
    """The subject page. Only it carries the Codigo that joins to the fetcher."""
    soup = BeautifulSoup(html, 'html.parser')
    fields = {}
    # The sidebar is a run of <strong>Label</strong><br><span>Value</span>.
    for label in soup.select('strong'):
        key = label.get_text(' ', strip=True).rstrip(':').lower()
        if key not in ("código", "ano", "créditos ects"):
            continue
        value = label.find_next('span')
        if value is not None:
            fields[key] = value.get_text(' ', strip=True)

    code = fields.get("código", "")
    if not re.match(r'^\d{6,10}$', code):
        return None  # a page without a usable code is a page we cannot join
    return {"code": code, "curricular_year_hint": fields.get("ano")}

# --- CRAWL ------------------------------------------------------------------

def load_cache(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def save_cache(path, cache):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError as e:
        print(f"Aviso: nao consegui gravar a cache ({e})")

def crawl(year, only_course=None, limit=None, cache_path=CACHE_FILE, fetcher=None):
    fetcher = fetcher or Fetcher()
    # unit_id -> code. Subjects are shared across branches and courses (tronco
    # comum), so this cache is what keeps the expensive step affordable.
    cache = load_cache(cache_path)
    cache_hits = 0

    listed = {}
    for degree_type in DEGREE_TYPES:
        html = fetcher.get(f"{BASE}/index", params={
            "submitform": "Pesquisar",
            "type": degree_type,
        })
        found = parse_index(html)
        print(f"{degree_type}: {len(found)} cursos")
        for course in found:
            listed.setdefault(course["uc_course_id"], course)

    courses = list(listed.values())
    if only_course:
        courses = [c for c in courses if c["uc_course_id"] == only_course]
    if limit:
        courses = courses[:limit]
    print(f"\nA percorrer {len(courses)} cursos ({year})...\n")

    result = []
    for index, course in enumerate(courses, 1):
        cid = course["uc_course_id"]
        print(f"[{index}/{len(courses)}] {course['name']} ({course['acronym']})")
        try:
            detail = parse_course(fetcher.get(f"{BASE}/course/{cid}/{year}"))
        except requests.RequestException as e:
            print(f"    erro: {e} - curso ignorado")
            continue

        entry = dict(course, slug=slugify(course["name"]), branches=[])

        for branch in detail["branches"]:
            bid = branch["uc_branch_id"]
            try:
                units = parse_programme(fetcher.get(
                    f"{BASE}/programme/{cid}/{year}",
                    params={"id_branch": bid},
                ))
            except requests.RequestException as e:
                print(f"    ramo {bid}: erro {e} - ignorado")
                continue

            resolved = []
            for unit in units:
                uid = unit["uc_unit_id"]
                if uid is None:
                    continue
                key = str(uid)
                if key in cache:
                    code = cache[key]
                    cache_hits += 1
                else:
                    try:
                        parsed = parse_unit(fetcher.get(
                            f"{BASE}/unit/{uid}/{bid}/{year}"
                        ))
                    except requests.RequestException as e:
                        print(f"    unidade {uid}: erro {e} - ignorada")
                        continue
                    code = parsed["code"] if parsed else None
                    cache[key] = code
                if code:
                    resolved.append(dict(unit, code=code))

            print(f"    ramo {bid} '{branch['name']}': {len(resolved)}/{len(units)} cadeiras")
            entry["branches"].append(dict(branch, units=resolved))

        result.append(entry)
        save_cache(cache_path, cache)  # survive an interrupted run

    print(f"\n{fetcher.count} pedidos, {cache_hits} unidades vindas da cache.")
    return result

# --- SUPABASE ---------------------------------------------------------------

class Supabase:
    """PostgREST straight over requests - no extra dependency for one job."""

    def __init__(self, url, service_key):
        self.url = url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        })

    def upsert(self, table, rows, on_conflict):
        """Returns the stored rows so callers can wire up foreign keys."""
        if not rows:
            return []
        stored = []
        for start in range(0, len(rows), 500):
            chunk = rows[start:start + 500]
            response = self.session.post(
                f"{self.url}/rest/v1/{table}",
                params={"on_conflict": on_conflict},
                headers={"Prefer": "return=representation,resolution=merge-duplicates"},
                json=chunk,
                timeout=60,
            )
            if not response.ok:
                raise RuntimeError(f"{table}: {response.status_code} {response.text[:300]}")
            stored.extend(response.json())
        return stored

def push(catalog, year, client):
    course_rows = [{
        "uc_course_id": c["uc_course_id"],
        "academic_year": year,
        "slug": c["slug"],
        "name": c["name"],
        "acronym": c["acronym"],
        "degree_type": c["degree_type"],
        "faculty": c["faculty"],
    } for c in catalog]
    courses = client.upsert("courses", course_rows, "uc_course_id,academic_year")
    course_ids = {c["uc_course_id"]: c["id"] for c in courses}
    print(f"  {len(courses)} cursos")

    branch_rows = []
    for course in catalog:
        for branch in course["branches"]:
            branch_rows.append({
                "course_id": course_ids[course["uc_course_id"]],
                "uc_branch_id": branch["uc_branch_id"],
                "name": branch["name"],
            })
    branches = client.upsert("branches", branch_rows, "course_id,uc_branch_id")
    branch_ids = {(b["course_id"], b["uc_branch_id"]): b["id"] for b in branches}
    print(f"  {len(branches)} ramos")

    # Subjects are global by code: the same cadeira serves every course that
    # lists it, so one upload of its shifts covers all of them at once.
    subject_rows = {}
    for course in catalog:
        for branch in course["branches"]:
            for unit in branch["units"]:
                subject_rows[unit["code"]] = {
                    "code": unit["code"],
                    "academic_year": year,
                    "name": unit["name"],
                    "ects": unit["ects"],
                    "uc_unit_id": unit["uc_unit_id"],
                }
    subjects = client.upsert(
        "subjects", list(subject_rows.values()), "code,academic_year"
    )
    subject_ids = {s["code"]: s["id"] for s in subjects}
    print(f"  {len(subjects)} cadeiras")

    unit_rows = {}
    for course in catalog:
        cid = course_ids[course["uc_course_id"]]
        for branch in course["branches"]:
            bid = branch_ids[(cid, branch["uc_branch_id"])]
            for unit in branch["units"]:
                # unique (branch_id, subject_id): keep one row per pair
                unit_rows[(bid, subject_ids[unit["code"]])] = {
                    "course_id": cid,
                    "branch_id": bid,
                    "subject_id": subject_ids[unit["code"]],
                    "curricular_year": unit["curricular_year"],
                    "semester": unit["semester"],
                    "is_optional": unit["is_optional"],
                }
    units = client.upsert(
        "course_units", list(unit_rows.values()), "branch_id,subject_id"
    )
    print(f"  {len(units)} entradas de plano curricular")

# --- CLI --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Rastreia o catalogo publico da UC (apps.uc.pt)."
    )
    parser.add_argument("--year", default=os.getenv("UC_ACADEMIC_YEAR") or academic_year(),
                        help="Ano letivo, ex: 2026-2027")
    parser.add_argument("--course", type=int, help="So este uc_course_id (ex: 362 = LEI)")
    parser.add_argument("--limit", type=int, help="So os primeiros N cursos")
    parser.add_argument("--out", help="Grava o catalogo em JSON")
    parser.add_argument("--push", action="store_true", help="Escreve no Supabase")
    parser.add_argument("--cache", default=CACHE_FILE)
    args = parser.parse_args()

    catalog = crawl(args.year, args.course, args.limit, args.cache)

    subjects = {u["code"] for c in catalog for b in c["branches"] for u in b["units"]}
    print(f"{len(catalog)} cursos, {len(subjects)} cadeiras distintas.")

    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
        print(f"Gravado em {args.out}")

    if args.push:
        url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            print("SUPABASE_URL e SUPABASE_SERVICE_KEY sao precisos para --push.")
            return 1
        # A crawl that collapsed is a parser break, not an empty university.
        # Refuse to overwrite a good catalogue with a bad one.
        if not args.course and not args.limit and len(catalog) < 100:
            print(f"So {len(catalog)} cursos - suspeito. Nada foi escrito.")
            return 1
        print("\nA escrever no Supabase...")
        push(catalog, args.year, Supabase(url, key))
        print("Feito.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
