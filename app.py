import os
from io import BytesIO
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    request,
    send_file,
    flash,
    abort
)
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    current_user
)
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.barcharts import VerticalBarChart
from sqlalchemy import func

from config import Config
from extensions import db
from models import (
    User,
    Patient,
    Analyse,
    DemandeAnalyse,
    ResultatAnalyse,
    RendezVousEtudiant
)
from utils.dossier_generator import generate_numero_dossier

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def role_required(*roles):
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("login"))

            if current_user.role not in roles:
                abort(403)

            return f(*args, **kwargs)
        return decorated
    return wrapper

# =========================
# DASHBOARD STATS
# =========================
def get_dashboard_stats():
    total_patients = Patient.query.count()
    total_analyses = DemandeAnalyse.query.count()

    total_revenus = db.session.query(
        func.coalesce(func.sum(DemandeAnalyse.prix_applique), 0)
    ).scalar()

    total_etudiants = Patient.query.filter_by(
        type_patient="etudiant"
    ).count()

    total_personnel = Patient.query.filter_by(
        type_patient="personnel"
    ).count()

    total_externes = Patient.query.filter_by(
        type_patient="externe"
    ).count()

    top_analyses = db.session.query(
        Analyse.nom,
        func.count(DemandeAnalyse.id).label("total")
    ).join(
        DemandeAnalyse,
        Analyse.id == DemandeAnalyse.analyse_id
    ).group_by(
        Analyse.nom
    ).order_by(
        func.count(DemandeAnalyse.id).desc()
    ).limit(5).all()

    # =========================
    # ⏱ TAT MOYEN (en minutes)
    # =========================
    tat_seconds = db.session.query(
        func.avg(
            func.strftime('%s', ResultatAnalyse.created_at) -
            func.strftime('%s', DemandeAnalyse.created_at)
        )
    ).join(
        DemandeAnalyse,
        ResultatAnalyse.patient_id == DemandeAnalyse.patient_id
    ).scalar()

    tat_moyen = round((tat_seconds or 0) / 60, 1)

    # =========================
    # 🧬 RESULTATS EN ATTENTE DE VALIDATION
    # =========================
    resultats_a_valider_count = ResultatAnalyse.query.filter_by(
        is_validated=False
    ).count()

    return {
        "total_patients": total_patients,
        "total_analyses": total_analyses,
        "total_revenus": total_revenus,
        "total_etudiants": total_etudiants,
        "total_personnel": total_personnel,
        "total_externes": total_externes,
        "top_analyses": top_analyses,
        "tat_moyen": tat_moyen,
        "resultats_a_valider_count": resultats_a_valider_count
    }

# =========================
# LISTE DES PATIENTS
# =========================
@app.route("/liste-patients")
@login_required
def liste_patients():
    patients = Patient.query.order_by(
        Patient.created_at.desc()
    ).all()

    return render_template(
        "liste_patients.html",
        patients=patients
    )


# =========================
# BASE ROUTES
# =========================
@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter_by(username=username).first()

        if user and user.password == password:
            login_user(user)
            return redirect(url_for("dashboard"))
        else:
            error = "Identifiant ou mot de passe incorrect."

    return render_template("login.html", error=error)


@app.route("/dashboard")
@login_required
def dashboard():
    stats = get_dashboard_stats()
    derniers_patients = Patient.query.order_by(
        Patient.id.desc()
    ).limit(5).all()

    return render_template(
        "dashboard.html",
        user=current_user,
        derniers_patients=derniers_patients,
        **stats
    )

# =========================
# PATIENTS EN ATTENTE DE RESULTATS
# =========================
@app.route("/patients/en-attente-resultats")
@login_required
def patients_en_attente_resultats():
    patients = (
        db.session.query(Patient)
        .join(DemandeAnalyse)
        .outerjoin(ResultatAnalyse)
        .filter(ResultatAnalyse.id == None)
        .distinct()
        .order_by(Patient.created_at.asc())
        .all()
    )

    now = datetime.utcnow()
    patients_data = []

    for patient in patients:
        attente_minutes = int(
            (now - patient.created_at).total_seconds() / 60
        )

        if attente_minutes > 120:
            priorite = "urgent"
        elif attente_minutes > 30:
            priorite = "normal"
        else:
            priorite = "recent"

        patients_data.append({
            "patient": patient,
            "attente_minutes": attente_minutes,
            "priorite": priorite
        })

    return render_template(
        "patients_resultats.html",
        patients_data=patients_data
    )

# =========================
# SEARCH PATIENT + HISTORY
# =========================
@app.route("/patients/search", methods=["GET", "POST"])
@login_required
def search_patient():
    patients = []
    query = ""

    if request.method == "POST":
        query = request.form.get("query", "").strip()

        if query:
            patients = Patient.query.filter(
                (Patient.nom.ilike(f"%{query}%")) |
                (Patient.prenom.ilike(f"%{query}%")) |
                (Patient.numero_dossier.ilike(f"%{query}%")) |
                (Patient.telephone.ilike(f"%{query}%"))
            ).order_by(Patient.created_at.desc()).all()

    return render_template(
        "search_patient.html",
        patients=patients,
        query=query
    )

# =========================
# RDV ÉTUDIANT QR PUBLIC
# =========================
@app.route("/rdv-etudiant", methods=["GET", "POST"])
def rdv_etudiant():
    if request.method == "POST":
        date_rdv, heure_rdv, numero_ordre = RendezVousEtudiant.prochain_creneau()

        if not date_rdv:
            return "❌ Le quota de 100 rendez-vous pour demain est atteint."

        bulletin = request.files.get("bulletin")
        bulletin_path = ""

        if bulletin and bulletin.filename:
            os.makedirs("static/uploads", exist_ok=True)
            filename = f"{datetime.utcnow().timestamp()}_{bulletin.filename}"
            bulletin_path = os.path.join("static/uploads", filename)
            bulletin.save(bulletin_path)

        rdv = RendezVousEtudiant(
            nom=request.form.get("nom"),
            prenom=request.form.get("prenom"),
            matricule=request.form.get("matricule"),
            telephone=request.form.get("telephone"),
            date_naissance=request.form.get("date_naissance"),
            bulletin_image=bulletin_path,
            date_rdv=date_rdv,
            heure_rdv=heure_rdv,
            numero_ordre=numero_ordre,
            statut="validé"
        )

        db.session.add(rdv)
        db.session.commit()

        return render_template(
            "rdv_confirmation.html",
            rdv=rdv
        )

    return render_template("prise_rdv.html")


# =========================
# NEW PATIENT + RDV ETUDIANT + DOSSIER DEFINITIF
# =========================
@app.route("/patients/new", methods=["GET", "POST"])
@login_required
@role_required("secretaire", "admin")
def new_patient():
    rdv_id = request.args.get("rdv_id")
    rdv = None

    if rdv_id:
        rdv = RendezVousEtudiant.query.get(rdv_id)

    if request.method == "POST":
        type_patient = request.form.get("type_patient")
        nom = request.form.get("nom", "").strip().upper()
        prenom = request.form.get("prenom", "").strip().upper()
        telephone = request.form.get("telephone", "").strip()
        matricule = request.form.get("matricule", "").strip()

        # ✅ RECHERCHE PATIENT EXISTANT = DOSSIER DÉFINITIF
        patient = Patient.query.filter(
            Patient.nom == nom,
            Patient.prenom == prenom,
            Patient.telephone == telephone
        ).first()

        # ✅ SI NOUVEAU PATIENT → CRÉATION DOSSIER
        if not patient:
            numero_dossier = generate_numero_dossier(type_patient)

            patient = Patient(
                numero_dossier=numero_dossier,
                type_patient=type_patient,
                nom=nom,
                prenom=prenom,
                date_naissance=request.form.get("date_naissance"),
                adresse=request.form.get("adresse"),
                telephone=telephone,
                matricule=matricule
            )

            db.session.add(patient)
            db.session.commit()

        # ✅ LIAISON RDV -> PATIENT
        if rdv and not rdv.patient_id:
            rdv.patient_id = patient.id
            rdv.statut = "terminé"
            db.session.commit()

        # ✅ TOUJOURS VERS ANALYSES
        return redirect(url_for("patient_analyses", patient_id=patient.id))

    # ✅ RDV VALIDÉS À TRAITER AU SECRÉTARIAT
    rdv_demain = RendezVousEtudiant.query.filter_by(
        statut="validé"
    ).order_by(
        RendezVousEtudiant.numero_ordre.asc()
    ).all()

    return render_template(
        "new_patient.html",
        rdv=rdv,
        rdv_demain=rdv_demain
    )

# =========================
# PRESCRIPTION ANALYSES
# =========================
@app.route("/patients/<int:patient_id>/analyses", methods=["GET", "POST"])
@login_required
def patient_analyses(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    analyses = Analyse.query.order_by(Analyse.categorie, Analyse.nom).all()

    if request.method == "POST":
        selected_ids = request.form.getlist("analyses")
        total = 0
        selected_analyses = []

        for analyse_id in selected_ids:
            analyse = Analyse.query.get(int(analyse_id))
            if not analyse:
                continue

            if patient.type_patient == "externe":
                prix = analyse.prix_externe
            elif patient.type_patient == "personnel":
                prix = analyse.prix_personnel
            else:
                prix = analyse.prix_etudiant

            db.session.add(DemandeAnalyse(
                patient_id=patient.id,
                analyse_id=analyse.id,
                prix_applique=prix
            ))

            total += prix
            selected_analyses.append(analyse)

        db.session.commit()

        return render_template(
            "facture_preview.html",
            patient=patient,
            analyses=selected_analyses,
            total=total
        )

    return render_template(
        "patient_analyses.html",
        patient=patient,
        analyses=analyses
    )


# =========================
# RESULT ENTRY
# =========================
@app.route("/patients/<int:patient_id>/resultats", methods=["GET", "POST"])
@login_required
@role_required("technicien", "admin")
def saisir_resultats(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    demandes = DemandeAnalyse.query.filter_by(patient_id=patient.id).all()

    if request.method == "POST":
        technicien = request.form.get("technicien")

        for demande in demandes:
            analyse = Analyse.query.get(demande.analyse_id)
            if not analyse:
                continue

            db.session.add(ResultatAnalyse(
                patient_id=patient.id,
                analyse_id=analyse.id,
                resultat=request.form.get(f"resultat_{analyse.id}"),
                unite=request.form.get(f"unite_{analyse.id}"),
                valeur_reference=request.form.get(f"reference_{analyse.id}"),
                technicien=technicien,
                valideur=None,
                is_validated=False
            ))

        db.session.commit()

        return render_template(
            "resultat_preview.html",
            patient=patient,
            demandes=demandes
        )

    return render_template(
        "saisir_resultats.html",
        patient=patient,
        demandes=demandes
    )

# =========================
# RESULTATS A VALIDER GROUPE PAR PATIENT
# =========================
@app.route("/resultats/a-valider")
@login_required
@role_required("admin")
def resultats_a_valider():
    resultats = (
        ResultatAnalyse.query
        .filter_by(is_validated=False)
        .order_by(
            ResultatAnalyse.patient_id.asc(),
            ResultatAnalyse.created_at.asc()
        )
        .all()
    )

    patients_groupes = {}

    for resultat in resultats:
        patient = Patient.query.get(resultat.patient_id)
        analyse = Analyse.query.get(resultat.analyse_id)

        if not patient or not analyse:
            continue

        if patient.id not in patients_groupes:
            patients_groupes[patient.id] = {
                "patient": patient,
                "technicien": resultat.technicien,
                "resultats": []
            }

        patients_groupes[patient.id]["resultats"].append({
            "id": resultat.id,
            "analyse": analyse.nom,
            "valeur": resultat.resultat
        })

    return render_template(
        "validation_resultats.html",
        patients_groupes=patients_groupes
    )


# =========================
# VALIDER UN RESULTAT
# =========================
@app.route("/resultats/<int:resultat_id>/valider")
@login_required
def valider_resultat(resultat_id):
    resultat = ResultatAnalyse.query.get_or_404(resultat_id)

    resultat.is_validated = True
    resultat.valideur = current_user.username

    db.session.commit()

    flash("✅ Résultat validé par le biologiste.", "success")
    return redirect(url_for("resultats_a_valider"))

# =========================
# PATIENT HISTORY
# =========================
@app.route("/patients/<int:patient_id>/historique")
@login_required
def historique_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)

    factures = DemandeAnalyse.query.filter_by(
        patient_id=patient.id
    ).order_by(
        DemandeAnalyse.created_at.desc()
    ).all()

    resultats = ResultatAnalyse.query.filter_by(
        patient_id=patient.id
    ).order_by(
        ResultatAnalyse.created_at.desc()
    ).all()

    return render_template(
        "historique_patient.html",
        patient=patient,
        factures=factures,
        resultats=resultats
    )

# FACTURE PDF
# =========================
@app.route("/patients/<int:patient_id>/facture-pdf")
@login_required
def facture_pdf(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    demandes = DemandeAnalyse.query.filter_by(patient_id=patient.id).all()

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    logo_labo = os.path.join("static", "logo_labo.png")
    logo_coud = os.path.join("static", "logo_coud.png")

    if os.path.exists(logo_labo):
        pdf.drawImage(logo_labo, 40, height - 90, width=90, height=50, preserveAspectRatio=True)

    if os.path.exists(logo_coud):
        pdf.drawImage(logo_coud, width - 130, height - 90, width=90, height=50, preserveAspectRatio=True)

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(width / 2, height - 60, "FACTURE LABORATOIRE COUD")
    pdf.setLineWidth(1)
    pdf.line(40, height - 105, width - 40, height - 105)

    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, height - 135, f"Patient : {patient.nom} {patient.prenom}")
    pdf.drawString(50, height - 155, f"Dossier : {patient.numero_dossier}")
    pdf.drawString(50, height - 175, f"Type : {patient.type_patient}")

    from datetime import datetime
    numero_facture = f"FAC-{patient.numero_dossier}"
    pdf.drawRightString(width - 50, height - 135, f"N° Facture : {numero_facture}")
    pdf.drawRightString(width - 50, height - 155, f"Date : {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    y = height - 220
    pdf.setFillColorRGB(0.9, 0.95, 1)
    pdf.rect(45, y, width - 90, 25, fill=1, stroke=0)

    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(55, y + 8, "Analyse")
    pdf.drawRightString(width - 60, y + 8, "Prix")

    y -= 30
    total = 0
    pdf.setFont("Helvetica", 11)

    for demande in demandes:
        analyse = Analyse.query.get(demande.analyse_id)
        if analyse:
            if y < 120:
                pdf.showPage()
                y = height - 80

            pdf.drawString(55, y, analyse.nom[:65])
            pdf.drawRightString(width - 60, y, f"{demande.prix_applique} FCFA")
            total += demande.prix_applique
            y -= 22

    y -= 10
    pdf.setFillColorRGB(0.91, 1, 0.94)
    pdf.rect(width - 230, y - 10, 180, 30, fill=1, stroke=0)

    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawRightString(width - 60, y, f"TOTAL : {total} FCFA")

    y -= 70
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, y, "Agent de caisse : __________________")
    pdf.drawRightString(width - 50, y, "Cachet / Signature labo")

    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawCentredString(width / 2, 30, "Laboratoire COUD - Université Cheikh Anta Diop de Dakar")

    pdf.save()
    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name=f"facture_{patient.numero_dossier}.pdf", mimetype="application/pdf")


# RESULT PDF PREMIUM + QR
# =========================
@app.route("/patients/<int:patient_id>/resultats-pdf")
@login_required
def resultats_pdf(patient_id):
    from datetime import datetime
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF

    patient = Patient.query.get_or_404(patient_id)
    resultats = ResultatAnalyse.query.filter_by(patient_id=patient.id).all()

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    logo_labo = os.path.join("static", "logo_labo.png")
    logo_coud = os.path.join("static", "logo_coud.png")
    bulletin_code = f"RES-{patient.numero_dossier}-{datetime.now().strftime('%Y%m%d%H%M')}"

    if os.path.exists(logo_labo):
        pdf.drawImage(logo_labo, 40, height - 90, width=90, height=50, preserveAspectRatio=True)

    if os.path.exists(logo_coud):
        pdf.drawImage(logo_coud, width - 130, height - 90, width=90, height=50, preserveAspectRatio=True)

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(width / 2, height - 60, "RÉSULTATS LABORATOIRE COUD")
    pdf.line(40, height - 105, width - 40, height - 105)

    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, height - 135, f"Patient : {patient.nom} {patient.prenom}")
    pdf.drawString(50, height - 155, f"Dossier : {patient.numero_dossier}")
    pdf.drawRightString(width - 50, height - 135, f"Date : {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    pdf.drawRightString(width - 50, height - 155, f"Code bulletin : {bulletin_code}")

    y = height - 210
    pdf.setFillColorRGB(0.9, 0.95, 1)
    pdf.rect(45, y, width - 90, 25, fill=1, stroke=0)

    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, y + 8, "Analyse")
    pdf.drawString(240, y + 8, "Résultat")
    pdf.drawString(340, y + 8, "Unité")
    pdf.drawString(430, y + 8, "Référence")

    y -= 28
    technicien = ""
    valideur = ""
    pdf.setFont("Helvetica", 10)

    for resultat in resultats:
        analyse = Analyse.query.get(resultat.analyse_id)
        if analyse:
            if y < 150:
                pdf.showPage()
                y = height - 80

            pdf.drawString(50, y, analyse.nom[:30])
            pdf.drawString(240, y, str(resultat.resultat or ""))
            pdf.drawString(340, y, str(resultat.unite or ""))
            pdf.drawString(430, y, str(resultat.valeur_reference or ""))

            technicien = resultat.technicien or ""
            valideur = resultat.valideur or ""
            y -= 22

    y -= 35
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, y, f"Technicien : {technicien}")
    pdf.drawRightString(width - 50, y, f"Biologiste valideur : {valideur}")

    y -= 35
    pdf.drawString(50, y, "Signature technicien : __________________")
    pdf.drawRightString(width - 50, y, "Signature biologiste : __________________")

    qr = QrCodeWidget(bulletin_code)
    bounds = qr.getBounds()
    qr_width = bounds[2] - bounds[0]
    qr_height = bounds[3] - bounds[1]

    drawing = Drawing(70, 70, transform=[70.0 / qr_width, 0, 0, 70.0 / qr_height, 0, 0])
    drawing.add(qr)
    renderPDF.draw(drawing, pdf, width - 120, 40)

    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(width - 210, 35, "QR de vérification")
    pdf.drawCentredString(width / 2, 30, "Laboratoire COUD - Université Cheikh Anta Diop de Dakar")

    pdf.save()
    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name=f"resultats_{patient.numero_dossier}.pdf", mimetype="application/pdf")


# =========================
# MONTHLY REPORT PDF 2 PAGES EXECUTIVE FINAL
# =========================
@app.route("/rapport-mensuel-pdf")
@login_required
@role_required("admin")
def rapport_mensuel_pdf():
    from datetime import datetime

    stats = get_dashboard_stats()

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    logo_labo = os.path.join("static", "logo_labo.png")
    logo_coud = os.path.join("static", "logo_coud.png")

    mois_annee = datetime.now().strftime("%m/%Y")

    # =========================
    # PAGE 1 HEADER PREMIUM ALIGNÉ
    # =========================
    top_y = height - 58

    # Logo gauche plus grand
    if os.path.exists(logo_labo):
        pdf.drawImage(
            logo_labo,
            55,
            top_y - 20,
            width=95,
            height=52,
            preserveAspectRatio=True,
            mask="auto"
        )

    # Logo droite plus grand
    if os.path.exists(logo_coud):
        pdf.drawImage(
            logo_coud,
            width - 150,
            top_y - 20,
            width=95,
            height=52,
            preserveAspectRatio=True,
            mask="auto"
        )

    # Titre réduit et plus élégant
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(
        width / 2,
        top_y,
        "RAPPORT MENSUEL LABORATOIRE COUD"
    )

    # Ligne de séparation propre
    pdf.setLineWidth(1)
    pdf.line(40, height - 92, width - 40, height - 92)

    # =========================
    # KPI PAGE 1
    # =========================
    y = height - 170
    box_w = 155
    box_h = 65
    gap = 18

    data_boxes = [
        ("Patients", str(stats["total_patients"]), (0.92, 0.96, 1)),
        ("Analyses", str(stats["total_analyses"]), (0.92, 1, 0.94)),
        ("Revenus", f"{stats['total_revenus']} FCFA", (1, 0.97, 0.92))
    ]

    x = 50
    for label, value, color in data_boxes:
        width_box = 180 if label == "Revenus" else box_w

        pdf.setFillColorRGB(*color)
        pdf.roundRect(x, y, width_box, box_h, 12, fill=1, stroke=0)

        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(x + 15, y + 42, label)

        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(x + 15, y + 16, value)

        x += width_box + gap

    # =========================
    # BLOCS ANALYTIQUES
    # =========================
    section_top = y - 110

    # Répartition
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, section_top, "RÉPARTITION DES TYPES")

    pdf.setFillColorRGB(0.96, 0.97, 1)
    pdf.roundRect(45, section_top - 120, 240, 105, 12, fill=1, stroke=0)

    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(65, section_top - 40, f"Étudiants : {stats['total_etudiants']}")
    pdf.drawString(65, section_top - 65, f"Personnel : {stats['total_personnel']}")
    pdf.drawString(65, section_top - 90, f"Externes : {stats['total_externes']}")

    # Top analyses
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(320, section_top, "TOP 5 ANALYSES")

    pdf.setFillColorRGB(0.96, 0.97, 1)
    pdf.roundRect(315, section_top - 120, 240, 105, 12, fill=1, stroke=0)

    line_y = section_top - 35
    pdf.setFont("Helvetica", 10)

    for i, (analyse, total) in enumerate(stats["top_analyses"][:5], start=1):
        pdf.drawString(330, line_y, f"{i}. {analyse[:22]}")
        pdf.drawRightString(540, line_y, f"{total}")
        line_y -= 18

    # =========================
    # SYNTHÈSE EXÉCUTIVE PREMIUM
    # =========================
    synth_y = section_top - 170

    pdf.setFillColorRGB(0.95, 0.97, 1)
    pdf.roundRect(45, synth_y - 70, width - 90, 70, 12, fill=1, stroke=0)

    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(60, synth_y - 20, "SYNTHÈSE EXÉCUTIVE")

    pdf.setFont("Helvetica", 11)

    ligne1 = (
        f"{stats['total_patients']} patients enregistrés, "
        f"{stats['total_analyses']} analyses réalisées"
    )

    ligne2 = (
        f"pour un revenu mensuel total de {stats['total_revenus']} FCFA."
    )

    pdf.drawString(60, synth_y - 42, ligne1)
    pdf.drawString(60, synth_y - 58, ligne2)

    # =========================
    # PAGE 2 DETAILS
    # =========================
    pdf.showPage()

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(
        width / 2,
        height - 60,
        "DÉTAILS FINANCIERS ET ANALYTIQUES"
    )

    pdf.line(40, height - 80, width - 40, height - 80)

    y2 = height - 120

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(50, y2, "Détail Top Analyses")

    y2 -= 30
    pdf.setFont("Helvetica", 11)

    for analyse, total in stats["top_analyses"]:
        pdf.drawString(60, y2, analyse)
        pdf.drawRightString(width - 60, y2, f"{total} demandes")
        y2 -= 22

    y2 -= 30
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(50, y2, "Validation Direction")

    y2 -= 50
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, y2, "Chef du laboratoire : __________________")
    pdf.drawRightString(width - 50, y2, "Signature / Cachet")

    # =========================
    # FOOTER PAGE 2
    # =========================
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawCentredString(
        width / 2,
        22,
        "Laboratoire COUD - UCAD | Rapport exécutif mensuel"
    )

    pdf.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"rapport_mensuel_labo_coud_{mois_annee.replace('/', '_')}.pdf",
        mimetype="application/pdf"
    )

# INIT CATALOGUE
# =========================
@app.route("/init-analyses")
@login_required
@role_required("admin")
def init_analyses():
    analyses_data = [
        ("Numération Formule Sanguine", "NFS", 6000, 2400, 0, "Hématologie"),
        ("VS", "VS", 2000, 800, 0, "Hématologie"),
        ("Réticulocytes", "RET", 5000, 2000, 0, "Hématologie"),
        ("Electrophorèse Hb", "ELHB", 10000, 4000, 0, "Hématologie"),
        ("Test d’Emmel", "EMMEL", 2000, 800, 0, "Hématologie"),
        ("Ferritine", "FERRI", 15000, 6000, 0, "Hématologie"),
        ("Fer sérique", "FER", 3000, 1200, 0, "Hématologie"),
        ("Glycémie", "GLY", 3000, 1200, 0, "Biochimie"),
        ("Glycémie post-prandiale", "GLYPP", 3000, 1200, 0, "Biochimie"),
        ("Triglycérides", "TRIG", 3000, 1200, 0, "Biochimie"),
        ("Acide urique", "AU", 3000, 1200, 0, "Biochimie"),
        ("Transaminases ALAT", "TGP", 3000, 1200, 0, "Biochimie"),
        ("Transaminases ASAT", "TGO", 3000, 1200, 0, "Biochimie"),
        ("Gamma GT", "GGT", 5000, 2000, 0, "Biochimie"),
        ("Phosphatases alcalines", "PAL", 5000, 2000, 0, "Biochimie"),
        ("Lipase", "LIP", 10000, 4000, 0, "Biochimie"),
        ("LDH", "LDH", 5000, 2000, 0, "Biochimie"),
        ("Ionogramme sanguin", "IONO", 8000, 3200, 0, "Biochimie"),
        ("Magnésémie", "MG", 3000, 1200, 0, "Biochimie"),
        ("Albuminémie", "ALB", 3000, 1200, 0, "Biochimie"),
        ("Protéinurie", "PROU", 3000, 1200, 0, "Biochimie"),
        ("CRP", "CRP", 4000, 1600, 0, "Immunologie"),
        ("Facteur rhumatoïde", "FR", 4000, 1600, 0, "Immunologie"),
        ("Waaler Rose", "WR", 4000, 1600, 0, "Immunologie"),
        ("IgE totales", "IGE", 15000, 6000, 0, "Immunologie"),
        ("IgG", "IGG", 15000, 6000, 0, "Immunologie"),
        ("IgM", "IGM", 15000, 6000, 0, "Immunologie"),
        ("ASLO", "ASLO", 4000, 1600, 0, "Immunologie"),
        ("TSH ultrasensible", "TSH", 10000, 4000, 0, "Hormonologie"),
        ("T3 libre", "T3L", 10000, 4000, 0, "Hormonologie"),
        ("T4 libre", "T4L", 10000, 4000, 0, "Hormonologie"),
        ("FSH", "FSH", 15000, 6000, 0, "Hormonologie"),
        ("LH", "LH", 15000, 6000, 0, "Hormonologie"),
        ("Prolactine", "PRL", 15000, 6000, 0, "Hormonologie"),
        ("Progestérone", "PROG", 15000, 6000, 0, "Hormonologie"),
        ("Œstradiol", "E2", 15000, 6000, 0, "Hormonologie"),
        ("Testostérone totale", "TEST", 15000, 6000, 0, "Hormonologie"),
        ("Hormone anti-müllérienne", "AMH", 30000, 12000, 0, "Hormonologie"),
        ("Antigène HBs", "AgHBs", 10000, 4000, 0, "Sérologie"),
        ("Antigène HBe", "AgHBe", 10000, 4000, 0, "Sérologie"),
        ("Anticorps Anti-HBs", "AcHBs", 10000, 4000, 0, "Sérologie"),
        ("Anticorps Anti-HBe", "AcHBe", 10000, 4000, 0, "Sérologie"),
        ("AC Anti VHC", "AcVHC", 10000, 4000, 0, "Sérologie"),
        ("AC Anti VHD", "AcVHD", 10000, 4000, 0, "Sérologie"),
        ("Sérologie syphilis", "BW", 10000, 4000, 0, "Sérologie"),
        ("Toxoplasmose IgM + IgG", "TOXO", 11000, 4400, 0, "Sérologie"),
        ("Test de Widal", "WIDAL", 5000, 2000, 0, "Sérologie"),
        ("Alpha foetoprotéine", "AFP", 15000, 6000, 0, "Marqueurs"),
        ("PSA", "PSA", 15000, 6000, 0, "Marqueurs"),
        ("ACE", "ACE", 15000, 6000, 0, "Marqueurs"),
        ("Pro-BNP", "BNP", 45000, 18000, 0, "Marqueurs"),
        ("Urines screening", "URINE", 4000, 1600, 0, "Urinaire"),
        ("Culot urinaire", "CULOT", 5000, 2000, 0, "Urinaire"),
        ("Glycosurie", "GLYU", 1000, 400, 0, "Urinaire"),
        ("Microalbuminurie", "MICRO", 8000, 3200, 0, "Urinaire"),
        ("Albuminurie 24h", "ALB24", 3000, 1200, 0, "Urinaire"),
    ]

    nouveaux = 0

    for nom, code, ext, pers, etud, cat in analyses_data:
        if not Analyse.query.filter_by(code=code).first():
            db.session.add(Analyse(
                nom=nom,
                code=code,
                prix_externe=ext,
                prix_personnel=pers,
                prix_etudiant=etud,
                categorie=cat
            ))
            nouveaux += 1

    db.session.commit()

    flash(
        f"✅ Catalogue synchronisé avec succès ({nouveaux} nouvelles analyses ajoutées).",
        "success"
    )

    return redirect(url_for("dashboard"))

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

        # 👨🏽‍💻 ADMIN
        if not User.query.filter_by(username="admin").first():
            db.session.add(User(
                username="admin",
                password="admin123",
                role="admin"
            ))

        # 👩🏽‍💼 SECRÉTAIRE
        if not User.query.filter_by(username="secretaire").first():
            db.session.add(User(
                username="secretaire",
                password="secret123",
                role="secretaire"
            ))

        # 🔬 TECHNICIEN LABO
        if not User.query.filter_by(username="technicien").first():
            db.session.add(User(
                username="technicien",
                password="tech123",
                role="technicien"
            ))

        db.session.commit()

    app.run(debug=True)