import os
import time
import smtplib
import html
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
import cloudscraper
import feedparser
from google import genai
from google.genai import types
from pydantic import BaseModel


# --- 1. Schémas de données Pydantic ---

class JobEvaluation(BaseModel):
    job_index: int
    title: str
    company: str
    relevance_score: int
    is_fit: bool
    summary_reason: str

class JobList(BaseModel):
    selected_jobs: list[JobEvaluation]


# --- 2. Configuration des sources ---

GREENHOUSE_COMPANIES = ["doctolib", "shifttechnology", "dataiku", "ecovadisfrance"]
LEVER_COMPANIES = ["qonto", "ledger-2", "swile", "spendesk", "agicap", "alan", "withings"]
ASHBY_COMPANIES = ["mistralai", "pigment", "pennylane", "owkin", "gleamer-ai", "nabla", "incepto"]
WORKABLE_COMPANIES = ["qare"]
PERSONIO_COMPANIES = [{"company": "PayFit", "token": "payfit-jobs"}]

WTTJ_SLUGS = [
    "kpmg-france", "mazars-france", "wavestone", "bearingpoint",
    "sia-partners", "capgemini-invent", "bnp-paribas", "societe-generale", "credit-agricole"
]

COMPANY_RSS_FEEDS = [
    {"company": "BearingPoint", "url": "https://fr.indeed.com/rss?q=BearingPoint+stage&l=Paris"},
    {"company": "Capgemini Invent", "url": "https://fr.indeed.com/rss?q=Capgemini+stage&l=Paris"},
    {"company": "Wavestone", "url": "https://fr.indeed.com/rss?q=Wavestone+stage&l=Paris"},
    {"company": "Sia Partners", "url": "https://fr.indeed.com/rss?q=Sia+Partners+stage&l=Paris"},
    {"company": "Banque de France", "url": "https://fr.indeed.com/rss?q=Banque+de+France+stage&l=Paris"},
    {"company": "AMF", "url": "https://fr.indeed.com/rss?q=AMF+stage&l=Paris"},
    {"company": "KPMG", "url": "https://fr.indeed.com/rss?q=KPMG+stage&l=Paris"},
    {"company": "PwC", "url": "https://fr.indeed.com/rss?q=PwC+stage&l=Paris"},
    {"company": "EY", "url": "https://fr.indeed.com/rss?q=EY+stage&l=Paris"},
    {"company": "Deloitte Risk", "url": "https://fr.indeed.com/rss?q=Deloitte+stage+risk&l=Paris"},
    {"company": "Mazars", "url": "https://fr.indeed.com/rss?q=Mazars+stage&l=Paris"},
    {"company": "Sanofi IA", "url": "https://fr.indeed.com/rss?q=Sanofi+stage+IA&l=France"},
    {"company": "GE Healthcare", "url": "https://fr.indeed.com/rss?q=GE+Healthcare+stage&l=France"},
    {"company": "Siemens Healthineers", "url": "https://fr.indeed.com/rss?q=Siemens+Healthineers+stage&l=France"},
    {"company": "Philips Sante", "url": "https://fr.indeed.com/rss?q=Philips+stage+sante&l=France"},
]

PROFILES = [
    {
        "name": "VisionMed",
        "email_env_var": "EMAIL_RECEIVER",
        "threshold": 50,
        "prompt": """
Tu es un expert en recrutement tech/santé. Tu évalues des offres pour un profil ciblant des stages en Computer Vision / Imagerie Médicale / IA appliquée à la Santé.

CRITÈRES D'ACCEPTATION :
- Stage de fin d'études ou césure en Machine Learning, Deep Learning, Traitement d'images, Computer Vision, Data Science ou IA appliquée à la santé, à l'imagerie ou à la biologie.
- Accepte la France entière (Île-de-France, Lyon, Marcy l'Étoile, Gentilly, Remote, etc.).
- Ne te limite PAS au terme exact "Computer Vision". Accepte : "Traitement d'images", "Deep Learning", "IA & Biomarqueurs", "Data Science Santé", "Machine Learning Engineer".
- Secteurs : Big Pharma, MedTech, Imagerie médicale, Startups IA santé.

EXCLUSIONS STRICTES : Marketing, commercial pur, support IT classique.

EVALUATION :
- 'job_index' : numéro exact de l'offre.
- Note de pertinence (0 à 100).
- 'is_fit' : True si score >= 50.
- Explication concise (1 phrase).
"""
    },
    {
        "name": "Julie",
        "email_env_var": "EMAIL_RECEIVER",
        "threshold": 50,
        "prompt": """
Tu es un expert en recrutement. Tu évalues des offres pour :
- Double diplôme Ingénieur INSA (Maths/IA/Data) + Sciences Po (Affaires publiques/Stratégie).
- Stage de 6 mois débutant début 2027 à Paris/Île-de-France.
- Domaines prioritaires : E-santé, santé publique, medtech, cybersécurité hospitalière, Product Management santé, conseil en stratégie santé / secteur public.
- Exclusions strictes : Commercial pur, marketing, stages < 4 mois.

EVALUATION :
- 'job_index' : numéro exact de l'offre.
- Note de pertinence (0 à 100).
- 'is_fit' : True si score >= 50.
- Explication concise (1 phrase).
"""
    },
    {
        "name": "Johan",
        "email_env_var": "EMAIL_RECEIVER_PARTNER",
        "threshold": 50,
        "prompt": """
Tu es un expert en recrutement finance / conseil. Tu évalues des offres pour :
- Master Corporate Strategy & Finance à Sciences Po Strasbourg, ex-auditeur bancaire KPMG.
- Stage 4 à 6 mois (Paris / Île-de-France).
- Secteurs ciblés :
  1. Banques & Institutions : Risk, Compliance, M&A, Inspection générale, Corporate Finance.
  2. Régulateurs : AMF, Banque de France, BCE.
  3. Agences de notation & ESG : Analyse ESG, Finance durable.
  4. Conseil : Big 4 (KPMG, PwC, EY, Deloitte), Mazars, conseil en stratégie/organisation bancaire.

EXCLUSIONS STRICTES : Comptabilité pure, paie, commercial.

EVALUATION :
- 'job_index' : numéro exact de l'offre.
- Note de pertinence (0 à 100).
- 'is_fit' : True si score >= 50.
- Explication concise (1 phrase).
"""
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# --- 3. Utilitaire & Modules de collecte ---

def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    clean_text = re.sub(r"<[^>]+>", " ", raw_html)
    return " ".join(html.unescape(clean_text).split())

def is_internship(title: str, summary: str = "") -> bool:
    keywords = ["stage", "intern", "internship", "cesure", "césure", "stagiaire", "trainee"]
    combined_text = f"{title} {summary}".lower()
    return any(k in combined_text for k in keywords)

def fetch_greenhouse(company: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
    collected = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            for job in res.json().get("jobs", []):
                title = job.get("title", "")
                if is_internship(title):
                    collected.append({
                        "company": company.capitalize(),
                        "title": title,
                        "link": job.get("absolute_url", ""),
                        "summary": clean_html(job.get("content", ""))[:1200]
                    })
    except Exception:
        pass
    return collected

def fetch_lever(company: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    collected = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            for job in res.json():
                title = job.get("text", "")
                commitment = job.get("categories", {}).get("commitment", "")
                if is_internship(title) or is_internship(commitment):
                    collected.append({
                        "company": company.replace("-2", "").capitalize(),
                        "title": title,
                        "link": job.get("hostedUrl", ""),
                        "summary": clean_html(job.get("descriptionPlain", ""))[:1200]
                    })
    except Exception:
        pass
    return collected

def fetch_ashby(company: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
    collected = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            for job in res.json().get("jobs", []):
                title = job.get("title", "")
                if is_internship(title):
                    collected.append({
                        "company": company.capitalize(),
                        "title": title,
                        "link": job.get("jobUrl", ""),
                        "summary": clean_html(job.get("descriptionHtml", ""))[:1200]
                    })
    except Exception:
        pass
    return collected

def fetch_workable(company: str) -> list[dict]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{company}"
    collected = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            for job in res.json().get("jobs", []):
                title = job.get("title", "")
                if is_internship(title):
                    collected.append({
                        "company": company.capitalize(),
                        "title": title,
                        "link": job.get("shortlink", ""),
                        "summary": clean_html(job.get("description", ""))[:1200]
                    })
    except Exception:
        pass
    return collected

def fetch_personio(info: dict) -> list[dict]:
    url = f"https://{info['token']}.personio.de/xml"
    collected = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for position in root.findall(".//position"):
                title = position.findtext("name", "")
                if is_internship(title):
                    job_id = position.findtext("id", "")
                    collected.append({
                        "company": info["company"],
                        "title": title,
                        "link": f"https://{info['token']}.personio.de/job/{job_id}",
                        "summary": clean_html(position.findtext("jobDescriptions", ""))[:1200]
                    })
    except Exception:
        pass
    return collected

def fetch_wttj_algolia(company_slug: str) -> list[dict]:
    algolia_url = "https://wv2989230y-dsn.algolia.net/1/indexes/bb_jobs_fr/query"
    params = {
        "x-algolia-agent": "Algolia for JavaScript (4.20.0)",
        "x-algolia-application-id": "WV2989230Y",
        "x-algolia-api-key": "a4d33923d242ef99e0f6c2a4c10648c3"
    }
    payload = {
        "query": "stage",
        "filters": f"company.slug:'{company_slug}'",
        "hitsPerPage": 20
    }
    collected = []
    try:
        res = requests.post(algolia_url, params=params, json=payload, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            for hit in res.json().get("hits", []):
                title = hit.get("name", "")
                summary = clean_html(hit.get("description", ""))[:1200]
                if is_internship(title, summary):
                    collected.append({
                        "company": company_slug.replace("-", " ").title(),
                        "title": title,
                        "link": f"https://www.welcometothejungle.com/fr/companies/{company_slug}/jobs/{hit.get('slug', '')}",
                        "summary": summary
                    })
    except Exception:
        pass
    return collected

def fetch_rss_cloudscraper(feed_info: dict) -> list[dict]:
    collected = []
    try:
        scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )
        resp = scraper.get(feed_info["url"], timeout=15)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                summary = clean_html(entry.get("summary", ""))[:1200]
                if is_internship(title, summary):
                    collected.append({
                        "company": feed_info["company"],
                        "title": title,
                        "link": entry.get("link", ""),
                        "summary": summary
                    })
    except Exception:
        pass
    return collected


# --- 4. Collecte parallèle ultra-rapide ---

def collect_all_jobs_parallel() -> list[dict]:
    all_jobs = []
    tasks = []

    with ThreadPoolExecutor(max_workers=25) as executor:
        for c in GREENHOUSE_COMPANIES:
            tasks.append(executor.submit(fetch_greenhouse, c))
        for c in LEVER_COMPANIES:
            tasks.append(executor.submit(fetch_lever, c))
        for c in ASHBY_COMPANIES:
            tasks.append(executor.submit(fetch_ashby, c))
        for c in WORKABLE_COMPANIES:
            tasks.append(executor.submit(fetch_workable, c))
        for p in PERSONIO_COMPANIES:
            tasks.append(executor.submit(fetch_personio, p))
        for w in WTTJ_SLUGS:
            tasks.append(executor.submit(fetch_wttj_algolia, w))
        for r in COMPANY_RSS_FEEDS:
            tasks.append(executor.submit(fetch_rss_cloudscraper, r))

        for future in as_completed(tasks):
            res = future.result()
            if res:
                all_jobs.extend(res)

    # Déduplication par lien
    unique_jobs = list({j["link"]: j for j in all_jobs}.values())
    print(f"✔ Collecte terminée : {len(unique_jobs)} offres uniques récupérées.")
    return unique_jobs


# --- 5. Évaluation Gemini & Envoi d'e-mails ---

def evaluate_with_gemini(client: genai.Client, jobs: list[dict], prompt: str, threshold: int, batch_size: int = 10) -> list[dict]:
    if not jobs:
        return []

    valid_results = []
    models = ["gemini-3.5-flash-lite", "gemini-2.5-flash"]

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        payload = f"Voici les offres à évaluer (lot {i//batch_size + 1}) :\n\n"
        for idx, j in enumerate(batch):
            payload += f"--- OFFRE {idx + 1} ---\nEntreprise: {j['company']}\nTitre: {j['title']}\nDescription: {j['summary']}\n\n"

        batch_done = False
        for model_name in models:
            if batch_done:
                break
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[prompt, payload],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=JobList,
                        temperature=0.1,
                    ),
                )
                parsed = JobList.model_validate_json(response.text)
                for eval_item in parsed.selected_jobs:
                    if eval_item.is_fit and eval_item.relevance_score >= threshold:
                        if 1 <= eval_item.job_index <= len(batch):
                            original = batch[eval_item.job_index - 1]
                            valid_results.append({
                                "title": eval_item.title,
                                "company": original["company"],
                                "url": original["link"],
                                "relevance_score": eval_item.relevance_score,
                                "summary_reason": eval_item.summary_reason
                            })
                batch_done = True
            except Exception as e:
                print(f"Erreur évaluation ({model_name}) : {e}")
                time.sleep(2)

    return valid_results

def send_email(matching_jobs: list[dict], receiver: str, user_name: str):
    if not matching_jobs:
        print(f"Aucune offre retenue pour {user_name}.")
        return

    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_PASSWORD")
    if not sender or not password:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 {len(matching_jobs)} nouvelle(s) offre(s) pour {user_name}"
    msg["From"] = sender
    msg["To"] = receiver

    html_content = f"<h2>Offres sélectionnées pour {user_name}</h2><ul style='list-style-type:none;padding-left:0;'>"
    for job in matching_jobs:
        color = "#00cc66" if job["relevance_score"] >= 80 else "#0055ff"
        html_content += f"""
          <li style="margin-bottom:15px;padding:12px;border-left:5px solid {color};background:#f8f9fa;">
            <b>{job['title']}</b> — <b>{job['company']}</b><br>
            <span style="color:{color};font-weight:bold;">Score : {job['relevance_score']}/100</span><br>
            <b>Analyse :</b> {job['summary_reason']}<br>
            👉 <a href="{job['url']}" target="_blank">Consulter l'offre</a>
          </li>
        """
    html_content += "</ul>"
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        print(f"E-mail envoyé à {receiver} pour {user_name} ({len(matching_jobs)} offres).")
    except Exception as e:
        print(f"Erreur envoi e-mail {user_name} : {e}")


# --- 6. Pipeline Principal ---

def run_pipeline():
    start_time = time.time()
    jobs = collect_all_jobs_parallel()
    print(f"Temps de collecte : {round(time.time() - start_time, 2)}s")

    if not jobs:
        return

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return

    client = genai.Client(api_key=api_key)

    for profile in PROFILES:
        receiver = os.environ.get(profile["email_env_var"])
        if not receiver:
            continue

        print(f"\nÉvaluation pour {profile['name']}...")
        matched = evaluate_with_gemini(
            client=client,
            jobs=jobs,
            prompt=profile["prompt"],
            threshold=profile["threshold"]
        )
        send_email(matched, receiver, profile["name"])

if __name__ == "__main__":
    run_pipeline()
