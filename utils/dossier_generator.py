from models import Patient


def generate_numero_dossier(type_patient):
    last_patient = Patient.query.order_by(Patient.id.desc()).first()
    next_id = 1 if not last_patient else last_patient.id + 1

    if type_patient == "personnel":
        return f"P{next_id:06d}"
    elif type_patient == "externe":
        return f"E{next_id:06d}"
    else:
        return f"{next_id:06d}"