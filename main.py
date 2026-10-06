import os
import time
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
    job_index: int
    title: str
    company: str
    relevance_score: int
    is_fit: bool
    summary_reason: str

class JobList(BaseModel):
    selected_jobs: list[JobEvaluation]


# --- 2. Configuration des cibles et profils ---

GREENHOUSE_COMPANIES = [
    # E-Santé / Medtech / Consulting (Julie & VisionMed)
    "doctolib",
    "alan",
    "lifen",
    "owkin",               # IA Santé / Imagerie
    "inceptomedical",      # IA / Imagerie Médicale
    "shifttechnology",     # IA / Assurtech
    "dataiku",             # Data / IA
    "mistralai",           # IA Gen / Tech
    "synapsemedicine",     # E-santé
    
    # Finance / Risk / ESG (Johan)
    "qonto",          
    "spendesk",       
    "pennylane",
    "pigment",
    "ledger",
    "carbon4finance",
    "swile",
]

LEVER_COMPANIES = [
    # MedTech & IA Santé (VisionMed & Julie)
    "gleamer",             # IA Imagerie médicale (Radiologie)
    "therapixel",          # IA / Mammographie / Imagerie
    "nabla",               # IA Copilote médical
    "withings",            # IoT Santé
    "qare",                # Télémédecine
    
    # ESG / Finance (Johan)
    "ecovadis",            # Notation ESG
    "mooncard",            # Fintech
    "agicap",              # Management financier
]

COMPANY_RSS_FEEDS = [
    # --- PROFIL JULIE (Santé, Medtech, Conseil public) ---
    {"company": "BearingPoint", "url": "https://fr.indeed.com/rss?q=BearingPoint+stage&l=Paris"},
    {"company": "Capgemini Invent", "url": "https://fr.indeed.com/rss?q=Capgemini+stage&l=Paris"},
    {"company": "Wavestone", "url": "https://fr.indeed.com/rss?q=Wavestone+stage&l=Paris"},
    {"company": "Sia Partners", "url": "https://fr.indeed.com/rss?q=Sia+Partners+stage&l=Paris"},
    {"company": "Deloitte Sante", "url": "https://fr.indeed.com/rss?q=Deloitte+stage+sante&l=Paris"},
    {"company": "Conseil Sante", "url": "https://fr.indeed.com/rss?q=stage+conseil+sante&l=Paris"},

    # --- PROFIL JOHAN (Finance, Risk, ESG, Régulation) ---
    {"company": "Banque de France", "url": "https://fr.indeed.com/rss?q=Banque+de+France+stage&l=Paris"},
    {"company": "AMF", "url": "https://fr.indeed.com/rss?q=AMF+stage&l=Paris"},
    {"company": "KPMG", "url": "https://fr.indeed.com/rss?q=KPMG+stage&l=Paris"},
    {"company": "PwC", "url": "https://fr.indeed.com/rss?q=PwC+stage&l=Paris"},
    {"company": "EY", "url": "https://fr.indeed.com/rss?q=EY+stage&l=Paris"},
    {"company": "Deloitte Risk", "url": "https://fr.indeed.com/rss?q=Deloitte+stage+risk&l=Paris"},
    {"company": "Mazars", "url": "https://fr.indeed.com/rss?q=Mazars+stage&l=Paris"},
    {"company": "BNP Paribas", "url": "https://fr.indeed.com/rss?q=BNP+Paribas+stage+conformite&l=Paris"},
    {"company": "Societe Generale", "url": "https://fr.indeed.com/rss?q=Societe+Generale+stage+risque&l=Paris"},
    {"company": "Credit Agricole", "url": "https://fr.indeed.com/rss?q=Credit+Agricole+stage+risk&l=Paris"},

    # --- PROFIL VISIONMED (IA, Imagerie, Big Pharma) ---
    {"company": "Sanofi IA", "url": "https://fr.indeed.com/rss?q=Sanofi+stage+IA&l=France"},
    {"company": "Sanofi Image", "url": "https://fr.indeed.com/rss?q=Sanofi+stage+image&l=France"},
    {"company": "Roche IA", "url": "https://fr.indeed.com/rss?q=Roche+stage+IA&l=France"},
    {"company": "GE Healthcare", "url": "https://fr.indeed.com/rss?q=GE+Healthcare+stage&l=France"},
    {"company": "Siemens Healthineers", "url": "https://fr.indeed.com/rss?q=Siemens+Healthineers+stage&l=France"},
    {"company": "Philips Sante", "url": "https://fr.indeed.com/rss?q=Philips+stage+sante&l=France"},
    {"company": "Computer Vision Sante", "url": "https://fr.indeed.com/rss?q=stage+computer+vision+sante&l=France"},
    {"company": "Imagerie Medicale IA", "url": "https://fr.indeed.com/rss?q=stage+imagerie+medicale&l=France"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

PROFILES = [
    {
        "name": "VisionMed",
        "email_env_var": "EMAIL_RECEIVER",  # Aligné sur l'adresse email de Julie
        "threshold": 50,
        "prompt": """
Tu es un expert en recrutement tech/santé. Tu évalues des offres pour un profil ciblant des stages en Computer Vision / Imagerie Médicale / IA appliquée à la Santé.

CRITÈRES D'ACCEPTATION :
- Stage de fin d'études ou césure en Machine Learning, Deep Learning, Traitement d'images, Computer Vision, Data Science ou IA appliquée à la santé, à l'imagerie ou à la biologie.
- Accepte la France entière (Île-de-France, Lyon, Marcy l'Étoile, Gentilly, Remote, etc.).
- Ne te limite PAS au terme exact "Computer Vision" dans le titre. Accepte : "Traitement d'images", "Deep Learning", "IA & Biomarqueurs", "Data Science Santé", "Machine Learning Engineer".
- Secteurs : Big Pharma (Sanofi, Roche...), MedTech, Imagerie médicale (GE Healthcare, Siemens, Philips), Startups IA santé (Owkin, Gleamer, Therapixel, Incepto Medical...).

EXCLUSIONS STRICTES : Marketing, commercial pur, affaires réglementaires pures, support IT classique.

EVALUATION :
- 'job_index' : numéro exact de l'offre dans le lot.
- Attribue une note de pertinence entre 0 et 100.
- Passe 'is_fit' à True si le score est >= 50.
- Explication concise (1 phrase).
"""
    },
    {
        "name": "Julie",
        "email_env_var": "EMAIL_RECEIVER",
        "threshold": 50,
        "prompt": """
Tu es un expert en recrutement. Tu évalues des offres pour le profil suivant :
- Double diplôme Ingénieur INSA (Maths applicables/IA/Data) + Sciences Po (Affaires publiques/Stratégie).
- Recherche : Stage de 6 mois débutant début 2027 à Paris/Île-de-France.
- Domaines prioritaires : E-santé, santé publique, medtech, cybersécurité hospitalière, Product Management santé, conseil en stratégie santé / secteur public.
- Exclusions strictes : Commercial pur, prospection, marketing, stages < 4 mois.

EVALUATION :
- 'job_index' : numéro exact de l'offre dans le lot.
- Attribue une note de pertinence entre 0 et 100.
- Passe 'is_fit' à True si le score est >= 50.
- Explication concise (1 sentence).
"""
    },
    {
        "name": "Johan",
        "email_env_var": "EMAIL_RECEIVER_PARTNER",
        "threshold": 50,
        "prompt": """
Tu es un expert en recrutement finance / conseil. Tu évalues des offres pour le profil suivant :
- Formation : Master Corporate Strategy & Finance à Sciences Po Strasbourg, ex-auditeur bancaire chez KPMG.
- Recherche : Stage 4 à 6 mois (Paris / Île-de-France).
- Secteurs ciblés :
  1. Banques & Institutions : Gestion des risques, Conformité/Compliance, M&A, Inspection générale, Finance d'entreprise.
  2. Régulateurs & Organismes publics : AMF, Banque de France, BCE.
  3. Agences de notation & ESG : Analyse ESG, Finance durable, Rating.
  4. Cabinets de Conseil : Big 4 (KPMG, PwC, EY, Deloitte), Mazars, conseil en stratégie/organisation bancaire.

EXCLUSIONS STRICTES : Comptabilité pure, paie, commercial/prospection.

EVALUATION :
- 'job_index' : numéro exact de l'offre dans le lot.
- Attribue une note de pertinence entre 0 et 100.
- Passe 'is_fit' à True si le score est >= 50.
- Explication concise (1 sentence).
"""
    }
]

# --- 3. Fonctions de collecte ---

def clean_html(raw_html: str) -> str:
    clean_text = re.sub(r"<[^>]+>", " ", raw_html)
    return " ".join(html.unescape(clean_text).split())

def is_internship(title: str, summary: str = "") -> bool:
    keywords = ["stage", "intern", "internship", "cesure", "césure", "stagiaire", "trainee"]
    combined_text = (title + " " + summary).lower()
    return any(k in combined_text for k in keywords)

def fetch_greenhouse_jobs(board_name: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_name}/jobs?content=true"
    collected = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=25)
        if res.status_code == 200:
            for job in res.json().get("jobs", []):
                title = job.get("title", "")
                location = job.get("location", {}).get("name", "")
                if is_internship(title) and any(loc in location for loc in ["Paris", "France", "Remote", "Lyon", "Gentilly", ""]):
                    collected.append({
                        "company": board_name.capitalize(),
                        "title": title,
                        "link": job.get("absolute_url", ""),
                        "summary": clean_html(job.get("content", ""))[:1200]
                    })
            print(f"✔ Greenhouse [{board_name}] : {len(collected)} stage(s) trouvé(s)")
        else:
            print(f"✖ Greenhouse [{board_name}] : Code HTTP {res.status_code}")
    except Exception as e:
        print(f"✖ Erreur Greenhouse [{board_name}] : {e}")
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
                if is_stage and any(loc in location for loc in ["Paris", "France", "Remote", "Issy", "Lyon", ""]):
                    collected.append({
                        "company": board_name.capitalize(),
                        "title": title,
                        "link": job.get("hostedUrl", ""),
                        "summary": clean_html(job.get("descriptionPlain", ""))[:1200]
                    })
            print(f"✔ Lever [{board_name}] : {len(collected)} stage(s) trouvé(s)")
        else:
            print(f"✖ Lever [{board_name}] : Code HTTP {res.status_code}")
    except Exception as e:
        print(f"✖ Erreur Lever [{board_name}] : {e}")
    return collected

def fetch_rss_jobs(feed_info: dict) -> list[dict]:
    collected = []
    try:
        resp = requests.get(feed_info["url"], headers=HEADERS, timeout=25)
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
            print(f"✔ RSS [{feed_info['company']}] : {len(collected)} offre(s) trouvée(s)")
        else:
            print(f"⚠️ RSS [{feed_info['company']}] bloqué (HTTP {resp.status_code})")
    except Exception as e:
        print(f"✖ Erreur RSS [{feed_info['company']}] : {e}")
    return collected
    
def collect_all_jobs() -> list[dict]:
    all_jobs = []

    print("--- Collecte Greenhouse ---")
    for company in GREENHOUSE_COMPANIES:
        all_jobs.extend(fetch_greenhouse_jobs(company))

    print("\n--- Collecte Lever ---")
    for company in LEVER_COMPANIES:
        all_jobs.extend(fetch_lever_jobs(company))

    print("\n--- Collecte Flux RSS ---")
    for feed_info in COMPANY_RSS_FEEDS:
        all_jobs.extend(fetch_rss_jobs(feed_info))

    unique_jobs = list({j["link"]: j for j in all_jobs}.values())
    print(f"\nTOTAL : {len(unique_jobs)} offres uniques collectées au total avant évaluation.")
    return unique_jobs

# --- 4. Évaluation Gemini ---

def evaluate_with_gemini(client: genai.Client, jobs: list[dict], prompt: str, threshold: int, batch_size: int = 10) -> list[dict]:
    if not jobs:
        return []

    valid_results = []
    # Gemini 3.5 Flash Lite comme modèle principal
    models_cascade = ["gemini-3.5-flash-lite", "gemini-2.5-flash"]
    
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        
        raw_payload = f"Voici les offres à évaluer (lot {i//batch_size + 1}) :\n\n"
        for idx, j in enumerate(batch):
            raw_payload += (
                f"--- OFFRE {idx + 1} ---\n"
                f"Entreprise: {j['company']}\n"
                f"Titre: {j['title']}\n"
                f"Description: {j['summary']}\n\n"
            )

        batch_success = False
        
        for model_name in models_cascade:
            if batch_success:
                break
                
            for attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=model_name, 
                        contents=[prompt, raw_payload],
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
                                original_job = batch[eval_item.job_index - 1]
                                valid_results.append({
                                    "title": eval_item.title,
                                    "company": original_job["company"],
                                    "url": original_job["link"],
                                    "relevance_score": eval_item.relevance_score,
                                    "summary_reason": eval_item.summary_reason
                                })
                                
                    batch_success = True
                    break
                    
                except Exception as e:
                    print(f"Erreur API {model_name} (lot {i//batch_size + 1}, essai {attempt+1}) : {e}")
                    time.sleep(5)

        time.sleep(2)

    return valid_results

# --- 5. Notification E-mail ---

def send_daily_email(matching_jobs: list[dict], receiver: str, user_name: str):
    if not matching_jobs:
        print(f"Aucune offre retenue pour {user_name}.")
        return

    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_PASSWORD")
    
    if not sender or not password:
        print("Erreur : Identifiants e-mail (EMAIL_SENDER / EMAIL_PASSWORD) non configurés.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 {len(matching_jobs)} nouvelle(s) offre(s) sélectionnée(s) pour {user_name}"
    msg["From"] = sender
    msg["To"] = receiver

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #222;">
        <h2>Offres sélectionnées pour {user_name}</h2>
        <p>Voici les opportunités identifiées aujourd'hui :</p>
        <ul style="list-style-type: none; padding-left: 0;">
    """

    for job in matching_jobs:
        color = "#00cc66" if job["relevance_score"] >= 80 else "#0055ff"
        
        html_content += f"""
          <li style="margin-bottom: 20px; padding: 12px; border-left: 5px solid {color}; background: #f8f9fa;">
            <b style="font-size: 16px;">{job['title']}</b> — <b>{job['company']}</b>
            <br>
            <span style="color: {color}; font-weight: bold;">Score : {job['relevance_score']}/100</span>
            <br>
            <b>Analyse :</b> {job['summary_reason']}
            <br>
            👉 <a href="{job['url']}" target="_blank" style="color: #0055ff; font-weight: bold; text-decoration: none;">Consulter l'offre</a>
          </li>
        """

    html_content += """
        </ul>
      </body>
    </html>
    """

    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        print(f"E-mail envoyé avec succès à {receiver} ({len(matching_jobs)} offre(s)).")
    except Exception as e:
        print(f"Erreur d'envoi d'e-mail pour {user_name} : {e}")

# --- 6. Pipeline et exécution ---

def run_pipeline():
    print("Démarrage de la collecte des offres...")
    jobs = collect_all_jobs()
    if not jobs:
        print("Aucune offre collectée.")
        return

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Erreur : GEMINI_API_KEY manquante.")
        return
        
    client = genai.Client(api_key=api_key)

    for profile in PROFILES:
        receiver_email = os.environ.get(profile["email_env_var"])
        if not receiver_email:
            print(f"Secret {profile['email_env_var']} manquant : profil {profile['name']} ignoré.")
            continue

        print(f"\n--- Évaluation en cours pour {profile['name']} ---")
        matched = evaluate_with_gemini(
            client=client,
            jobs=jobs,
            prompt=profile["prompt"],
            threshold=profile["threshold"],
            batch_size=10
        )
        
        send_daily_email(
            matching_jobs=matched,
            receiver=receiver_email,
            user_name=profile["name"]
        )

if __name__ == "__main__":
    run_pipeline()
