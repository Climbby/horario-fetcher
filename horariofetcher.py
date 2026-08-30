import os
import sys
import json
import re
import time
import getpass
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

from ucshared import academic_year, normalize_semester, parse_academic_year

CONFIG_FILE = "fetcher_settings.json"

# Bumped when the shape of the output file changes, so the site can tell an
# old classes_filtered.json from a new one.
FETCHER_VERSION = "2"

def app_dir():
    """Folder the tool lives in - next to the .exe once frozen, else the source."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# A shared .env is optional: it lets a returning user skip every prompt.
load_dotenv(os.path.join(app_dir(), ".env"))

# horarioPicker reads classes_filtered.json. By default we drop it next to the
# tool so it can be imported into the site; set the env var to write elsewhere.
OUTPUT_PATH = os.getenv(
    "HORARIO_PICKER_JSON",
    os.path.join(app_dir(), "classes_filtered.json"),
)

# --- SETTINGS THE USER DOESN'T HAVE TO EDIT BY HAND ---

def config_path():
    return os.path.join(app_dir(), CONFIG_FILE)

def load_config():
    """Remembers the previous run's answers. Never holds the password."""
    try:
        with open(config_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def save_config(cfg):
    try:
        with open(config_path(), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"Aviso: nao consegui gravar as preferencias ({e})")

def choose(title, labels, remembered=None):
    """Numbered menu. `remembered` is a label offered as the ENTER default."""
    if len(labels) == 1:
        return 0

    default = labels.index(remembered) if remembered in labels else None

    print(f"\n{title}")
    for i, label in enumerate(labels, 1):
        marker = "  <- da ultima vez" if i - 1 == default else ""
        print(f"  {i}. {label}{marker}")

    hint = f" (ENTER = {default + 1})" if default is not None else ""
    while True:
        try:
            raw = input(f"Escolhe [1-{len(labels)}]{hint}: ").strip()
        except EOFError:
            if default is None:
                raise
            return default
        if not raw and default is not None:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(labels):
            return int(raw) - 1
        print("  Opcao invalida.")

def resolve_credentials(cfg):
    """.env wins, then the remembered username; the password is never stored."""
    env_user, env_pass = os.getenv("UC_USERNAME"), os.getenv("UC_PASSWORD")
    if env_user and env_pass:
        # A fully populated .env means an unattended run, so ask nothing.
        return env_user, env_pass

    username = env_user or cfg.get("username", "")

    if username:
        typed = input(f"Utilizador [{username}] (ENTER para confirmar): ").strip()
        username = typed or username
    else:
        while not username:
            username = input("Utilizador (ex: uc2024123456@student.uc.pt): ").strip()

    return username, env_pass or getpass.getpass("Password: ")

# --- SCRAPING ---

def login(driver, user, pwd):
    """Handles the login process."""
    print("\nA entrar no InforEstudante...")
    driver.get("https://inforestudante.uc.pt/nonio/security/login.do")

    wait = WebDriverWait(driver, 10)
    user_field = wait.until(EC.presence_of_element_located((By.NAME, "username")))
    pass_field = driver.find_element(By.NAME, "password")

    user_field.send_keys(user)
    pass_field.send_keys(pwd)
    pass_field.send_keys(Keys.RETURN)

    try:
        wait.until(EC.presence_of_element_located((By.ID, "areaMenuConteudo")))
    except TimeoutException:
        raise RuntimeError(
            "Login falhou. Verifica o utilizador e a password."
        )
    print("Login confirmado!")

# Each matricula (degree enrolment) is a row with its own "Selecionar" link.
MATRICULA_XPATH = (
    "//a[contains(@href, 'listaInscricoes.do') and contains(., 'Selecionar')]"
)

def matricula_label(link):
    """The degree name sits in the row around the 'Selecionar' link."""
    try:
        row = link.find_element(By.XPATH, "./ancestor::tr[1]")
        text = row.text
    except WebDriverException:
        return "Matricula sem nome"
    text = re.sub(r'\bSelecionar\b', '', text)
    return re.sub(r'\s+', ' ', text).strip() or "Matricula sem nome"

def select_matricula(driver, cfg):
    """Picks the degree to scrape. Only asks when there is more than one.

    Returns the chosen row's label, which is also where the academic year
    tends to hide.
    """
    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, MATRICULA_XPATH))
        )
    except TimeoutException:
        # Single matricula: the site skips this page entirely.
        return cfg.get("matricula")

    links = driver.find_elements(By.XPATH, MATRICULA_XPATH)
    if not links:
        return cfg.get("matricula")

    # Read every label before clicking - the click makes the elements stale.
    labels = [matricula_label(link) for link in links]
    index = choose("Em que curso queres o horario?", labels, cfg.get("matricula"))

    cfg["matricula"] = labels[index]
    links[index].click()
    print(f"Curso: {labels[index]}")
    return labels[index]

def navigate_to_class_list(driver, cfg):
    """Navigates to the class list."""
    wait = WebDriverWait(driver, 10)
    print("A navegar pelo menu...")

    xpath_menu = "//a[contains(., 'Inscrições em Turmas')]"

    try:
        try:
            menu_button = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_menu)))
            menu_button.click()
        except Exception:
            # Fallback for mobile/tablet view menu structure
            parent_menu = driver.find_element(By.XPATH, "//span[contains(text(), 'Balcão Académico')]")
            parent_menu.click()
            time.sleep(0.5)
            menu_button = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_menu)))
            menu_button.click()

        matricula = select_matricula(driver, cfg)

        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "displaytable")))
        print("Lista de cadeiras carregada.")
        return matricula

    except Exception as e:
        print(f"Navegacao falhou: {e}")
        raise

def get_regime(soup):
    """Extracts the Regime (Semester) from the page."""
    # Find the label "Regime:" and get the next cell
    label = soup.find('td', class_='label', string=re.compile(r'Regime'))
    if label:
        content = label.find_next_sibling('td')
        if content:
            return content.get_text(strip=True)
    return ""

def get_class_basic_info(soup):
    """Extracts Name and ID."""
    subtitle = soup.find('td', class_='subtitle')
    if subtitle:
        text = subtitle.get_text(strip=True)
        match = re.search(r'^(.*?) - (\d+)$', text)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    return None, None

def parse_shifts_table(soup):
    """Parses the static table containing shift codes and vacancies."""
    shifts = []
    tables = soup.find_all('table', class_='displaytable')
    for table in tables:
        header = table.find('th', class_='cellheader')
        if header and "Turma" in header.get_text():
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 4:
                    raw_text = cells[0].get_text(strip=True)
                    shift_code = re.sub(r'\d\)$', '', raw_text) # Clean code

                    vagas = cells[3].get_text(strip=True)

                    type_match = re.match(r'([A-Za-z]+)(\d+)', shift_code)

                    # The row's checkbox carries the shift id, which is also the
                    # id of this shift's events in the calendar.
                    checkbox = row.find('input', attrs={'name': 'visibilidade'})
                    shift_id = checkbox.get('value') if checkbox else None

                    shifts.append({
                        "id": shift_id,
                        "code": shift_code,
                        "type": type_match.group(1) if type_match else shift_code,
                        "number": type_match.group(2) if type_match else None,
                        "available_slots": vagas,
                        "schedule": [] # To be filled later
                    })
    return shifts

# InforEstudante runs FullCalendar v5+, which tags each day column with a
# fc-day-<abbr> class instead of positioning events by pixel offset.
FC_DAY_CLASSES = {
    "fc-day-mon": "Segunda",
    "fc-day-tue": "Terça",
    "fc-day-wed": "Quarta",
    "fc-day-thu": "Quinta",
    "fc-day-fri": "Sexta",
    "fc-day-sat": "Sábado",
    "fc-day-sun": "Domingo",
}

def get_event_day(event):
    """Resolves an event's weekday from the day column it sits in."""
    cell = event.find_parent('td', attrs={'data-date': True})
    if not cell:
        return None
    for cls in cell.get('class', []):
        if cls in FC_DAY_CLASSES:
            return FC_DAY_CLASSES[cls]
    return None

def parse_calendar_events(soup):
    """Parses the events of the currently displayed week."""
    events = []

    # v5 renders each event as <a class="fc-event ..."> with the time and title
    # in nested divs; the old markup used <div class="fc-event"> with spans.
    for event in soup.select('a.fc-event'):
        day = get_event_day(event)
        if not day:
            continue

        time_el = event.select_one('.fc-event-time')
        time_text = time_el.get_text(strip=True) if time_el else ""
        start, end = "", ""
        if "-" in time_text:
            parts = time_text.split("-")
            start, end = parts[0].strip(), parts[1].strip()

        title_el = event.select_one('.fc-event-title')
        full_title = title_el.get_text(strip=True) if title_el else ""

        # Titles look like "ECACPL6(DEI - C6.5)" - the room is the trailing ().
        room = ""
        clean_title = full_title
        room_match = re.search(r'\((.*?)\)$', full_title)
        if room_match:
            room = room_match.group(1)
            clean_title = full_title[:room_match.start()].strip()

        events.append({
            "id": event.get('id'),
            "raw_title": clean_title,
            "day": day,
            "start": start,
            "end": end,
            "room": room,
            "unique_key": f"{day}|{start}|{end}|{clean_title}|{room}"
        })
    return events

def go_to_next_week(driver):
    """Clicks the next button and waits for the week title to change."""
    wait = WebDriverWait(driver, 5)

    # 1. Get current week title
    try:
        title_el = driver.find_element(By.CLASS_NAME, "fc-toolbar-title")
        old_title = title_el.text
    except Exception:
        return False # Can't navigate if calendar not found

    # 2. Click Next
    try:
        next_btn = driver.find_element(By.CLASS_NAME, "fc-next-button")
        next_btn.click()
    except Exception:
        return False

    # 3. Wait for title to be DIFFERENT from old_title
    try:
        wait.until(lambda d: d.find_element(By.CLASS_NAME, "fc-toolbar-title").text != old_title)
        # Small buffer for events to render after title change
        time.sleep(1)
        return True
    except Exception:
        print(" -> Aviso: timeout a carregar a semana seguinte.")
        return False

# --- TWO-PASS SCRAPE ---

def collect_class_index(driver):
    """First pass: every class with its name and regime, without the calendar.

    Reading the regimes off the site is what lets any course pick its semester
    from a list, instead of matching a string typed by hand.
    """
    links = driver.find_elements(By.XPATH, "//a[contains(@href, 'inscrever.do?args=')]")
    class_urls = [link.get_attribute('href') for link in links]
    print(f"\nEncontrei {len(class_urls)} cadeiras. A ler os semestres...")

    entries = []
    for url in class_urls:
        driver.get(url)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "displaytable"))
            )
        except TimeoutException:
            pass

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        name, _ = get_class_basic_info(soup)
        entries.append({
            "url": url,
            "name": name or "(sem nome)",
            "regime": get_regime(soup),
        })
    return entries

def choose_semester(entries, cfg):
    """Asks which regime to export, using the regimes actually on the site."""
    regimes = sorted({e['regime'] for e in entries if e['regime']})
    if not regimes:
        return ""

    env_semester = os.getenv("UC_SEMESTER")
    if env_semester:
        if any(env_semester in r for r in regimes):
            print(f"\nSemestre (do .env): {env_semester}")
            return env_semester
        print(f"\nAviso: UC_SEMESTER={env_semester!r} nao existe nesta matricula.")

    counts = {r: sum(1 for e in entries if e['regime'] == r) for r in regimes}
    labels = [
        f"{r} ({counts[r]} cadeira{'s' if counts[r] != 1 else ''})" for r in regimes
    ]

    # The remembered value is a bare regime, but choose() matches against the
    # labels, so it has to be translated before it can be offered as default.
    remembered = cfg.get("semester")
    remembered_label = labels[regimes.index(remembered)] if remembered in regimes else None
    index = choose("Que semestre queres exportar?", labels, remembered_label)

    chosen = regimes[index]
    cfg["semester"] = chosen
    return chosen

def select_for_semester(entries, semester):
    """Exact match first, so a broader .env string still works as a fallback."""
    exact = [e for e in entries if e['regime'] == semester]
    return exact or [e for e in entries if semester in e['regime']]

def scrape_class(driver, entry):
    """Second pass: shifts plus three weeks of calendar for one class."""
    driver.get(entry['url'])
    time.sleep(1) # Base wait for page load

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    name, cid = get_class_basic_info(soup)
    shifts = parse_shifts_table(soup)

    print(f" -> {name} (3 semanas)...")

    consolidated_events = {}
    for i in range(3):
        current_soup = BeautifulSoup(driver.page_source, 'html.parser')
        for ev in parse_calendar_events(current_soup):
            consolidated_events[ev['unique_key']] = ev

        if i < 2 and not go_to_next_week(driver):
            break

    # The calendar shows the student's whole timetable, so it also holds events
    # from other courses. Each event's id is its shift id, which lets us pick
    # out this course's events exactly instead of guessing from the title.
    events_by_shift = {}
    for event in consolidated_events.values():
        if event['id']:
            events_by_shift.setdefault(event['id'], []).append(event)

    matched = 0
    for shift in shifts:
        for event in events_by_shift.get(shift['id'], []):
            shift['schedule'].append({
                "day": event['day'],
                "start": event['start'],
                "end": event['end'],
                "room": event['room']
            })
            matched += 1

    empty = [sh['code'] for sh in shifts if not sh['schedule']]
    print(f"    {matched} eventos em {len(shifts)} turnos", end="")
    print(f" (sem horario: {', '.join(empty)})" if empty else "")

    return {"class_name": name, "class_id": cid, "shifts": shifts}

def resolve_academic_year(label):
    """The matricula row usually spells the year out; if not, derive it."""
    return (
        os.getenv("UC_ACADEMIC_YEAR")
        or parse_academic_year(label)
        or academic_year()
    )

def build_document(all_classes_data, matricula, semester):
    """The file the site imports.

    The classes array is byte-for-byte what earlier versions wrote; the meta
    block is what lets the site match this upload to the UC catalogue without
    asking the student anything.
    """
    return {
        "meta": {
            "institution": "UC",
            "course_raw": matricula or "",
            "academic_year": resolve_academic_year(matricula),
            "semester": normalize_semester(semester),
            "semester_raw": semester,
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "fetcher_version": FETCHER_VERSION,
        },
        "classes": all_classes_data,
    }

# --- BROWSER ---

def build_driver():
    """Selenium Manager resolves chromedriver, so only Chrome has to be installed."""
    chrome_options = Options()
    chrome_options.add_argument("--window-size=1920,1080")

    if os.getenv("HORARIO_HEADLESS", "").lower() in ("1", "true", "yes"):
        chrome_options.add_argument("--headless=new")

    keep_open = os.getenv("HORARIO_KEEP_BROWSER", "").lower() in ("1", "true", "yes")
    if keep_open:
        chrome_options.add_experimental_option("detach", True)

    try:
        return webdriver.Chrome(options=chrome_options), keep_open
    except WebDriverException as e:
        print("\nNao consegui abrir o Chrome.")
        print("Instala o Google Chrome (https://www.google.com/chrome) e tenta outra vez.")
        print(f"Detalhe: {e}")
        return None, keep_open

def main():
    cfg = load_config()
    username, password = resolve_credentials(cfg)

    driver, keep_open = build_driver()
    if driver is None:
        return

    try:
        login(driver, username, password)
        cfg["username"] = username

        matricula = navigate_to_class_list(driver, cfg)

        entries = collect_class_index(driver)
        if not entries:
            print("Nao encontrei nenhuma cadeira nesta matricula.")
            return

        semester = choose_semester(entries, cfg)
        selected = select_for_semester(entries, semester)
        if not selected:
            print(f"\nNenhuma cadeira no regime {semester!r}.")
            return

        save_config(cfg)

        print(f"\nA recolher {len(selected)} cadeiras de {semester}:")
        all_classes_data = [scrape_class(driver, entry) for entry in selected]

        output_dir = os.path.dirname(OUTPUT_PATH)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        document = build_document(all_classes_data, matricula, semester)
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(document, f, indent=4, ensure_ascii=False)

        print(f"\nPronto! {len(all_classes_data)} cadeiras guardadas em:")
        print(f"  {OUTPUT_PATH}")
        print("\nAbre https://climbby.github.io/horarioPicker/ e carrega em")
        print("'Importar horario' para escolheres este ficheiro. Ao importar")
        print("podes partilha-lo, e o teu curso passa a estar la para todos.")

    except Exception as e:
        print("\n--- ERRO ---")
        print(e)
    finally:
        if not keep_open:
            driver.quit()

if __name__ == "__main__":
    main()
    if getattr(sys, 'frozen', False):
        input("\nPrime ENTER para fechar...")
