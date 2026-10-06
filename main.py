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
    title: str
    company: str
    url: str
    relevance_score: int  # 0 à 100
    is_fit: bool
    summary_reason: str

class JobList(BaseModel):
    selected_jobs: list[JobEvaluation]


# --- 2. Configuration des cibles et profils ---

GREENHOUSE_COMPANIES = [
    "doctolib",
    "qonto",          
    "spendesk",       
    "carbon4finance", 
    "owkin",          # IA & Biotech / MedTech (utilise souvent Greenhouse)
]

LEVER_COMPANIES = [
    "withings",
    "nabla",
    "ecovadis",       
    "mooncard",       
    "gleamer",        # Pépite française en Computer Vision pour l'imagerie médicale
    "therapixel",     # IA appliquée à la radiologie
]

COMPANY_RSS_FEEDS = [
    # Profil santé / conseil public
    {"company": "BearingPoint", "url": "https://fr.indeed.com/rss?q=BearingPoint+stage&l=Paris"},
    {"company": "Capgemini Invent", "url": "https://fr.indeed.com/rss?q=Capgemini+stage+sante&l=Paris"},
    {"company": "Wavestone", "url": "https://fr.indeed.com/rss?q=Wavestone+stage&l=Paris"},
    {"company": "Sia Partners", "url": "https://fr.indeed.com/rss?q=Sia+Partners+stage&l=Paris"},
    {"company": "Deloitte Sante", "url": "https://fr.indeed.com/rss?q=Deloitte+stage+sante&l=Paris"},

    # Profil Johan : Régulateurs, Banques, Big 4, ESG
    {"company": "Banque de France", "url": "https://fr.indeed.com/rss?q=Banque+de+France+stage&l=Paris"},
    {"company": "AMF", "url": "https://fr.indeed.com/rss?q=AMF+stage+marches+financiers&l=Paris"},
    {"company": "KPMG Audit/Risk", "url": "https://fr.indeed.com/rss?q=KPMG+stage+audit+bancaire+risk&l=Paris"},
    {"company": "PwC Risk/ESG", "url": "https://fr.indeed.com/rss?q=PwC+stage+risk+compliance&l=Paris"},
    {"company": "EY Audit/Risk", "url": "https://fr.indeed.com/rss?q=EY+stage+banque+conformite&l=Paris"},
    {"company": "Deloitte Risk", "url": "https://fr.indeed.com/rss?q=Deloitte+stage+risk+regulatory&l=Paris"},
    {"company": "Mazars ESG/Banque", "url": "https://fr.indeed.com/rss?q=Mazars+stage+conformite+banque&l=Paris"},
    {"company": "BNP Paribas Risk", "url": "https://fr.indeed.com/rss?q=BNP+Paribas+stage+conformite+risque&l=Paris"},
    {"company": "Societe Generale Risk", "url": "https://fr.indeed.com/rss?q=Societe+Generale+stage+compliance+risque&l=Paris"},
    {"company": "Credit Agricole CIB", "url": "https://fr.indeed.com/rss?q=Credit+Agricole+stage+risk+ESG&l=Paris"},
    {"company": "Notation ESG", "url": "https://fr.indeed.com/rss?q=stage+analyste+ESG+finance+durable&l=Paris"},
    
    # NOUVEAUX : Profil Computer Vision Médicale & Big Pharma (Sanofi, Roche, etc.)
    {"company": "Sanofi IA/Data", "url": "https://fr.indeed.com/rss?q=Sanofi+stage+(IA+OR+vision+OR+data+OR+%22machine+learning%22)&l=France"},
    {"company": "Roche IA/Data", "url": "https://fr.indeed.com/rss?q=Roche+stage+(IA+OR+vision+OR+data+OR+%22deep+learning%22)&l=France"},
    {"company": "Philips Healthcare", "url": "https://fr.indeed.com/rss?q=Philips+stage+(IA+OR+vision+OR+image+OR+algorithm)&l=France"},
    {"company": "Siemens Healthineers", "url": "https://fr.indeed.com/rss?q=Siemens+Healthineers+stage+(IA+OR+vision+OR+deep+learning)&l=France"},
    {"company": "GE Healthcare", "url": "https://fr.indeed.com/rss?q=GE+Healthcare+stage+(IA+OR+vision+OR+deep+learning)&l=France"},
    {"company": "Recherche Générique CV Médical", "url": "https://fr.indeed.com/rss?q=stage+(%22computer+vision%22+OR+%22vision+par+ordinateur%22+OR+%22imagerie%22)+(sante+OR+medical+OR+hopital)&l=France"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

PROFILES = [
    {
        "name": "VisionMed",
        "email_env_var": "EMAIL_RECEIVER_VISION",  # <-- Ajoute cette variable d'environnement pour ton email
        "threshold": 65,
        "prompt": """
Tu es un expert en recrutement tech et IA. Tu évalues des offres pour un profil ciblant des stages en "Computer Vision Médicale".
- Recherche : Stage de fin d'études ou césure en Machine Learning, Deep Learning, Computer Vision appliqué à la santé.
- Secteurs : Imagerie médicale, Big Pharma, MedTech, IA en santé (ex: Sanofi, Roche, Owkin, Gleamer).
- Exclusions strictes : Stages en marketing, finance, réglementaire pur sans technique, IT support ou dev web classique.
Pour chaque offre fournie :
- Attribue une note de pertinence entre 0 et 100.
- Passe 'is_fit' à True UNIQUEMENT si le score est >= 65 et que le rôle implique de l'IA/Computer Vision/Data Science.
- Fournis une explication concise (1 phrase) de l'adéquation ou du refus.
- Assure-toi de recopier EXACTEMENT le lien fourni dans le champ 'url'.
"""
    },
    {
        "name": "Julie",
        "email_env_var": "EMAIL_RECEIVER",
        "threshold": 50,
        "prompt": """
Tu es un expert en recrutement. tu évalues des offres pour le profil suivant :
- Double diplôme Ingénieur INSA (Mathématiques appliquées/IA/Data) + Sciences Po (Affaires publiques/Stratégie d'entreprise).
- Recherche : Stage de 6 mois débutant en février/mars/avril 2027 à Paris/Île-de-France.
- Domaines prioritaires : E-santé, santé publique, medtech, SSI/cybersécurité hospitalière, Product Management santé, transformation du secteur public / santé.
- Exclusions strictes : Rôles purement commerciaux, prospection, optimisation des prix / pricing pur, Marketing, stages courts (< 4 mois).
Pour chaque offre fournie :
- Attribue une note de pertinence entre 0 et 100.
- Passe 'is_fit' à True UNIQUEMENT si le score est >= 70.
- Fournis une explication concise (1 phrase) de l'adéquation ou du refus.
- Assure-toi de recopier EXACTEMENT le lien fourni dans le champ 'url'.
"""
    },
    {
        "name": "Johan",
        "email_env_var": "EMAIL_RECEIVER_PARTNER",
        "threshold": 50,
        "prompt": """
Tu es un expert en recrutement. Tu évalues des offres pour le profil suivant :
- Formation : Étudiant en Master "Corporate Strategy and Finance in Europe" à Sciences Po Strasbourg, actuellement en césure.
- Recherche : Stage 4 à 6 mois pour 2027. Localisation : Paris et périphérie (Île-de-France).
- Secteurs Ciblés : Banques, Agences de notation (ESG), Autorités de régulation, Cabinets de conseil (Big 4 & Big 3).
Pour chaque offre fournie :
- Attribue une note de pertinence entre 0 et 100.
- Passe 'is_fit' à True UNIQUEMENT si le score est >= 70.
- Fournis une explication concise (1 phrase) de l'adéquation ou du refus.
- Assure-toi de recopier EXACTEMENT le lien fourni dans le champ 'url'.
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
                if is_internship(title) and any(loc in location for loc in ["Paris", "France", "Remote", "Lyon"]):
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
            for entry in feed.entries[:10]: # On en prend jusqu'à 10 par flux
                title = entry.get("title", "")
                summary = clean_html(entry.get("summary", ""))[:1200]
                if is_internship(title, summary):
                    collected.append({
                        "company": feed_info["company"],
                        "title": title,
                        "link": entry.get("link", ""),
                        "summary": summary
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
    print(f"{len(unique_jobs)} offres uniques collectées avant évaluation.")
    return unique_jobs

# --- 4. Évaluation Gemini avec Batching et Retry ---

def evaluate_with_gemini(client: genai.Client, jobs: list[dict], prompt: str, threshold: int, batch_size: int = 15) -> list[JobEvaluation]:
    if not jobs:
        return []

    valid_jobs = []
    
    # Traitement par lots (batching) pour éviter le timeout et la limite de tokens
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        
        raw_payload = f"Voici les offres à évaluer (lot {i//batch_size + 1}) :\n\n"
        for idx, j in enumerate(batch):
            raw_payload += (
                f"--- OFFRE {idx + 1} ---\n"
                f"Entreprise: {j['company']}\n"
                f"Titre: {j['title']}\n"
                f"Lien (url): {j['link']}\n"
                f"Description: {j['summary']}\n\n"
            )

        # Mécanisme de Retry en cas de surcharge de l'API (Erreur 503, 429...)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Utilisation d'un modèle flash à jour
                response = client.models.generate_content(
                    model="gemini-1.5-flash", 
                    contents=[prompt, raw_payload],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=JobList,
                        temperature=0.1,
                    ),
                )
                
                parsed = JobList.model_validate_json(response.text)
                
                for job_eval in parsed.selected_jobs:
                    if job_eval.is_fit and job_eval.relevance_score >= threshold:
                        valid_jobs.append(job_eval)
                        
                break # Sortie de la boucle retry si succès
                
            except Exception as e:
                print(f"Erreur API Gemini (lot {i//batch_size + 1}, essai {attempt+1}/{max_retries}) : {e}")
                time.sleep(5) # Pause avant de réessayer
        
        # Pause obligatoire entre deux lots pour respecter les quotas de l'API gratuite/standard
        time.sleep(3)

    return valid_jobs

# --- 5. Notification E-mail ---

def send_daily_email(matching_jobs: list[JobEvaluation], receiver: str, user_name: str):
    if not matching_jobs:
        print(f"Aucune offre retenue pour {user_name}.")
        return

    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_PASSWORD")
    
    if not sender or not password:
        print("Erreur : Identifiants e-mail (EMAIL_SENDER / EMAIL_PASSWORD) non configurés.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 {len(matching_jobs)} nouvelle(s) offre(s) de stage pour {user_name}"
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
        # Code couleur en fonction du score
        color = "#00cc66" if job.relevance_score >= 85 else "#0055ff"
        
        html_content += f"""
          <li style="margin-bottom: 20px; padding: 12px; border-left: 5px solid {color}; background: #f8f9fa;">
            <b style="font-size: 16px;">{job.title}</b> — <b>{job.company}</b>
            <br>
            <span style="color: {color}; font-weight: bold;">Score : {job.relevance_score}/100</span>
            <br>
            <b>Analyse :</b> {job.summary_reason}
            <br>
            👉 <a href="{job.url}" target="_blank" style="color: #0055ff; font-weight: bold; text-decoration: none;">Consulter l'offre</a>
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
        print("Aucune offre collectée sur l'ensemble des sources.")
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
            batch_size=12 # Ajustable si l'API est trop capricieuse
        )
        
        send_daily_email(
            matching_jobs=matched,
            receiver=receiver_email,
            user_name=profile["name"]
        )

if __name__ == "__main__":
    run_pipeline()
