import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html
import re
import feedparser
import requests
from google import genai
from google.genai import types
from pydantic import BaseModel

# --- 1. Schémas de données structurés ---

class JobEvaluation(BaseModel):
    title: str
    company: str
    url: str
    relevance_score: int  # 0 à 100
    is_fit: bool
    summary_reason: str

class JobList(BaseModel):
    selected_jobs: list[JobEvaluation]


# --- 2. Configuration des cibles et profil ---

GREENHOUSE_COMPANIES = [
    "doctolib",
]

LEVER_COMPANIES = [
    "withings",
    "nabla",
]

COMPANY_RSS_FEEDS = [
    {"company": "BearingPoint", "url": "https://fr.indeed.com/rss?q=company:BearingPoint+stage&l=Paris"},
    {"company": "Capgemini Invent", "url": "https://fr.indeed.com/rss?q=company:%22Capgemini+Invent%22+stage&l=Paris"},
    {"company": "Wavestone", "url": "https://fr.indeed.com/rss?q=company:Wavestone+stage&l=Paris"},
    {"company": "Sia Partners", "url": "https://fr.indeed.com/rss?q=company:%22Sia+Partners%22+stage&l=Paris"},
    {"company": "Deloitte", "url": "https://fr.indeed.com/rss?q=company:Deloitte+stage+secteur+public+sante&l=Paris"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

PROMPT_PROFIL = """
Tu es un assistant de recrutement expert. Tu dois évaluer des offres pour le profil suivant :
- Double diplôme : Ingénieur (Mathématiques appliquées, Data, IA) + Sciences Po (Affaires publiques, Stratégie d'entreprise).
- Actuellement en stage chez Airbus Defence and Space (gestion de projet, KPI, data/IA, spécifications).
- Recherche : Stage de césure (6 mois) débutant en mars 2027 à Paris / Île-de-France.
- Domaines prioritaires : E-santé, santé publique, medtech, SSI/cybersécurité hospitalière, Product Management santé, transformation du secteur public / santé.
- Exclusions strictes : Rôles purement commerciaux, prospection/sales, optimisation des prix / pricing pur, stages courts (< 6 mois).

Pour chaque offre fournie :
- Attribue une note de pertinence entre 0 et 100.
- Passe 'is_fit' à True UNIQUEMENT si le score est >= 70.
- Fournis une explication concise (1 phrase) de l'adéquation ou du refus.
"""

# --- 3. Fonctions de collecte ---

def clean_html(raw_html: str) -> str:
    clean_text = re.sub(r"<[^>]+>", " ", raw_html)
    return " ".join(html.unescape(clean_text).split())

def is_internship(title: str) -> bool:
    keywords = ["stage", "intern", "internship", "cesure", "césure", "stagiaire", "trainee"]
    title_lower = title.lower()
    return any(k in title_lower for k in keywords)

def fetch_greenhouse_jobs(board_name: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_name}/jobs?content=true"
    collected = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=25)
        if res.status_code == 200:
            for job in res.json().get("jobs", []):
                title = job.get("title", "")
                location = job.get("location", {}).get("name", "")
                if is_internship(title) and any(loc in location for loc in ["Paris", "France", "Remote"]):
                    collected.append({
                        "company": board_name.capitalize(),
                        "title": title,
                        "link": job.get("absolute_url", ""),
                        "summary": clean_html(job.get("content", ""))[:1200]
                    })
    except Exception as e:
        print(f"Erreur Greenhouse ({board_name}) : {e}")
    return collected

def fetch_lever_jobs(board_name: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{board_name}?mode=json"
    collected = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=25)
        if res.status_code == 200:
            for job in res.json():
                title = job.get("text", "")
                location = job.get("categories", {}).get("location", "")
                commitment = job.get("categories", {}).get("commitment", "")
                
                is_stage = is_internship(title) or is_internship(commitment)
                if is_stage and any(loc in location for loc in ["Paris", "France", "Remote", "Issy"]):
                    collected.append({
                        "company": board_name.capitalize(),
                        "title": title,
                        "link": job.get("hostedUrl", ""),
                        "summary": clean_html(job.get("descriptionPlain", ""))[:1200]
                    })
    except Exception as e:
        print(f"Erreur Lever ({board_name}) : {e}")
    return collected

def fetch_rss_jobs(feed_info: dict) -> list[dict]:
    collected = []
    try:
        resp = requests.get(feed_info["url"], headers=HEADERS, timeout=25)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:8]:
                title = entry.get("title", "")
                if is_internship(title):
                    collected.append({
                        "company": feed_info["company"],
                        "title": title,
                        "link": entry.get("link", ""),
                        "summary": clean_html(entry.get("summary", ""))[:1200]
                    })
    except Exception as e:
        print(f"Erreur RSS ({feed_info['company']}) : {e}")
    return collected

def collect_all_jobs() -> list[dict]:
    all_jobs = []

    for company in GREENHOUSE_COMPANIES:
        all_jobs.extend(fetch_greenhouse_jobs(company))

    for company in LEVER_COMPANIES:
        all_jobs.extend(fetch_lever_jobs(company))

    for feed_info in COMPANY_RSS_FEEDS:
        all_jobs.extend(fetch_rss_jobs(feed_info))

    unique_jobs = list({j["link"]: j for j in all_jobs}.values())

    # Offre de test pour valider la chaîne d'envoi d'e-mail au premier run
    unique_jobs.append({
        "company": "BearingPoint (Offre Test)",
        "title": "Stage Consultant Public & Health Services",
        "link": "https://www.bearingpoint.com",
        "summary": "Stage de fin d'études ou césure de 6 mois à Paris à partir de mars 2027. Missions de cadrage, transformation numérique et appui aux politiques de santé publique et ministères."
    })

    print(f"{len(unique_jobs)} offres potentielles collectées avant évaluation.")
    return unique_jobs

# --- 4. Évaluation Gemini ---

def evaluate_with_gemini(jobs: list[dict]) -> list[JobEvaluation]:
    if not jobs:
        return []

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    raw_payload = "Voici les offres collectées aujourd'hui :\n\n"
    for i, j in enumerate(jobs):
        raw_payload += (
            f"--- OFFRE {i+1} ---\n"
            f"Entreprise: {j['company']}\n"
            f"Titre: {j['title']}\n"
            f"Lien: {j['link']}\n"
            f"Description: {j['summary']}\n\n"
        )

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[PROMPT_PROFIL, raw_payload],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=JobList,
            temperature=0.2,
        ),
    )

    parsed = JobList.model_validate_json(response.text)
    return [job for job in parsed.selected_jobs if job.is_fit]

# --- 5. Notification E-mail ---

def send_daily_email(matching_jobs: list[JobEvaluation]):
    if not matching_jobs:
        print("Aucune offre retenue par l'évaluation.")
        return

    sender = os.environ["EMAIL_SENDER"]
    password = os.environ["EMAIL_PASSWORD"]
    receiver = os.environ["EMAIL_RECEIVER"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 {len(matching_jobs)} nouvelle(s) offre(s) de stage ciblée(s)"
    msg["From"] = sender
    msg["To"] = receiver

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #222;">
        <h2>Offres sélectionnées pour ton profil (Santé / Conseil / E-Santé)</h2>
        <p>Voici les opportunités identifiées aujourd'hui :</p>
        <ul style="list-style-type: none; padding-left: 0;">
    """

    for job in matching_jobs:
        html_content += f"""
          <li style="margin-bottom: 20px; padding: 12px; border-left: 4px solid #0055ff; background: #f8f9fa;">
            <b style="font-size: 16px;">{job.title}</b> — <b>{job.company}</b>
            <br>
            <b>Score de pertinence :</b> {job.relevance_score}/100
            <br>
            <b>Analyse :</b> {job.summary_reason}
            <br>
            👉 <a href="{job.url}" target="_blank" style="color: #0055ff; font-weight: bold;">Consulter l'offre</a>
          </li>
        """

    html_content += """
        </ul>
      </body>
    </html>
    """

    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())
    print(f"E-mail envoyé avec succès contenant {len(matching_jobs)} offre(s).")

# --- Point d'entrée ---

if __name__ == "__main__":
    jobs = collect_all_jobs()
    evaluated_jobs = evaluate_with_gemini(jobs)
    send_daily_email(evaluated_jobs)