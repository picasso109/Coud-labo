from datetime import datetime, date, timedelta
from extensions import db
from flask_login import UserMixin


# =========================
# UTILISATEURS
# =========================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(50), default='admin')


# =========================
# PATIENTS
# =========================
class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_dossier = db.Column(db.String(20), unique=True, nullable=False)
    type_patient = db.Column(db.String(20), nullable=False)  # etudiant/personnel/externe
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    date_naissance = db.Column(db.String(20))
    adresse = db.Column(db.String(255))
    telephone = db.Column(db.String(20))
    matricule = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    demandes = db.relationship('DemandeAnalyse', backref='patient', lazy=True)
    resultats = db.relationship('ResultatAnalyse', backref='patient', lazy=True)


# =========================
# RENDEZ-VOUS ÉTUDIANTS QR
# =========================
class RendezVousEtudiant(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    matricule = db.Column(db.String(50), nullable=False)
    telephone = db.Column(db.String(20), nullable=False)
    date_naissance = db.Column(db.String(20))

    bulletin_image = db.Column(db.String(255))
    qr_code = db.Column(db.String(255))

    date_rdv = db.Column(db.Date, nullable=False)
    heure_rdv = db.Column(db.String(20), nullable=False)

    numero_ordre = db.Column(db.Integer, nullable=False)

    statut = db.Column(
        db.String(20),
        default="en_attente"
    )  # en_attente / validé / terminé

    patient_id = db.Column(
        db.Integer,
        db.ForeignKey('patient.id'),
        nullable=True
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def prochain_creneau():
        """
        Retourne automatiquement un créneau du lendemain
        entre 8h et 11h avec quota 100 patients max
        """
        demain = date.today() + timedelta(days=1)

        total_rdv = RendezVousEtudiant.query.filter_by(
            date_rdv=demain
        ).count()

        if total_rdv >= 100:
            return None, None, None

        numero = total_rdv + 1

        # Répartition entre 8h et 11h
        minutes_total = 180  # 3h
        slot_minutes = max(1, minutes_total // 100)

        heure_base = 8
        total_minutes = (numero - 1) * slot_minutes

        heure = heure_base + (total_minutes // 60)
        minute = total_minutes % 60

        heure_str = f"{heure:02d}:{minute:02d}"

        return demain, heure_str, numero


# =========================
# CATALOGUE DES ANALYSES
# =========================
class Analyse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(50), unique=True, nullable=False)

    # Tarification
    prix_externe = db.Column(db.Integer, nullable=False)
    prix_personnel = db.Column(db.Integer, nullable=False)
    prix_etudiant = db.Column(db.Integer, default=0)

    # Classement
    categorie = db.Column(db.String(100))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    demandes = db.relationship('DemandeAnalyse', backref='analyse', lazy=True)
    resultats = db.relationship('ResultatAnalyse', backref='analyse', lazy=True)


# =========================
# DEMANDES D'ANALYSES
# =========================
class DemandeAnalyse(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    patient_id = db.Column(
        db.Integer,
        db.ForeignKey('patient.id'),
        nullable=False
    )

    analyse_id = db.Column(
        db.Integer,
        db.ForeignKey('analyse.id'),
        nullable=False
    )

    prix_applique = db.Column(db.Integer, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =========================
# RESULTATS D'ANALYSES
# =========================
class ResultatAnalyse(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    patient_id = db.Column(
        db.Integer,
        db.ForeignKey('patient.id'),
        nullable=False
    )

    analyse_id = db.Column(
        db.Integer,
        db.ForeignKey('analyse.id'),
        nullable=False
    )

    resultat = db.Column(db.String(255))
    unite = db.Column(db.String(50))
    valeur_reference = db.Column(db.String(100))
    commentaire = db.Column(db.Text)

    technicien = db.Column(db.String(100))
    valideur = db.Column(db.String(100))

    is_validated = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)