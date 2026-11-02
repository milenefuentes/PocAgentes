"""
Generador de datos sinteticos para superbank_db.
Genera 10 CSVs coherentes entre si (mismas llaves) en ./data/

Uso:
    pip install faker
    python scripts/generate_data.py
"""
import csv
import os
import random
from datetime import date, datetime, timedelta

from faker import Faker

random.seed(42)
fake = Faker("es_ES")  # nombres/formato en español
Faker.seed(42)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(RAIZ, "data")
os.makedirs(OUT_DIR, exist_ok=True)

# --- Config de volumen (mediano) ---
N_BRANCHES = 12
N_EMPLOYEES = 60
N_CUSTOMERS = 400
N_ACCOUNTS_PER_CUSTOMER = (1, 2)   # min, max
N_CARDS_PER_CUSTOMER = (0, 2)
N_TXN_PER_ACCOUNT = (5, 40)
N_CARD_TXN_PER_CARD = (5, 30)
N_LOANS = 150
N_TICKETS = 250

CIUDADES_PERU = [
    ("Lima", "Lima"), ("Arequipa", "Arequipa"), ("Trujillo", "La Libertad"),
    ("Chiclayo", "Lambayeque"), ("Piura", "Piura"), ("Cusco", "Cusco"),
    ("Iquitos", "Loreto"), ("Huancayo", "Junin"), ("Tacna", "Tacna"),
    ("Chimbote", "Ancash"), ("Ica", "Ica"), ("Puno", "Puno"),
]

CIUDADES_EXTRANJERO = [
    "Miami, USA", "Madrid, España", "Santiago, Chile", "Bogota, Colombia",
    "Buenos Aires, Argentina", "Sao Paulo, Brasil", "Ciudad de Mexico, Mexico",
]

OCUPACIONES = [
    "Ingeniero", "Comerciante", "Profesor", "Enfermero", "Abogado",
    "Contador", "Disenador", "Chofer", "Medico", "Estudiante",
    "Emprendedor", "Administrador", "Arquitecto", "Vendedor",
]

MERCHANT_CATEGORIES = [
    "Supermercado", "Restaurante", "Farmacia", "Electronica", "Ropa",
    "Combustible", "Educacion", "Entretenimiento", "Viajes", "Salud",
    "Servicios", "Hogar",
]

TXN_TYPES = ["deposito", "retiro", "transferencia", "pago de servicio"]
CHANNELS = ["app", "cajero", "ventanilla", "web"]
ACCOUNT_TYPES = ["ahorros", "corriente", "plazo fijo"]
CARD_TYPES = ["debito", "credito"]
LOAN_TYPES = ["personal", "hipotecario", "vehicular", "negocio"]
LOAN_STATUS = ["activo", "pagado", "en mora", "cancelado"]
ISSUE_TYPES = [
    "tarjeta bloqueada", "reclamo de cobro", "consulta de saldo",
    "solicitud de prestamo", "actualizacion de datos", "fraude reportado",
    "problema con app", "cierre de cuenta",
]
TICKET_STATUS = ["abierto", "en proceso", "resuelto", "cerrado"]


def random_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, max(delta, 0)))


def write_csv(filename, fieldnames, rows):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {filename}: {len(rows)} filas")


def main():
    print("Generando datos sinteticos...")

    # 1. branches
    branches = []
    for i in range(1, N_BRANCHES + 1):
        city, state = CIUDADES_PERU[i % len(CIUDADES_PERU)]
        branches.append({
            "branch_id": i,
            "branch_name": f"Agencia {city} {i}",
            "city": city,
            "state": state,
            "opened_date": random_date(date(2005, 1, 1), date(2022, 1, 1)),
            "ifsc_code": f"SBK-{city[:3].upper()}-{i:03d}",
        })
    write_csv("branches.csv", list(branches[0].keys()), branches)

    # 2. employees
    employees = []
    roles = ["Cajero", "Asesor de negocios", "Gerente de agencia", "Analista de riesgo"]
    for i in range(1, N_EMPLOYEES + 1):
        employees.append({
            "employee_id": i,
            "name": fake.name(),
            "branch_id": random.randint(1, N_BRANCHES),
            "role": random.choice(roles),
            "hire_date": random_date(date(2010, 1, 1), date(2025, 6, 1)),
            "salary": round(random.uniform(1800, 9000), 2),
        })
    write_csv("employees.csv", list(employees[0].keys()), employees)

    # 3. customers
    customers = []
    for i in range(1, N_CUSTOMERS + 1):
        city, state = random.choice(CIUDADES_PERU)
        gender = random.choice(["M", "F"])
        name = fake.name_male() if gender == "M" else fake.name_female()
        join_date = random_date(date(2015, 1, 1), date(2026, 8, 1))
        customers.append({
            "customer_id": i,
            "name": name,
            "gender": gender,
            "date_of_birth": random_date(date(1955, 1, 1), date(2005, 1, 1)),
            "city": city,
            "state": state,
            "phone": f"9{random.randint(10000000, 99999999)}",
            "email": fake.unique.email(),
            "occupation": random.choice(OCUPACIONES),
            "annual_income": round(random.uniform(12000, 180000), 2),
            "join_date": join_date,
            "credit_score": random.randint(300, 850),
        })
    write_csv("customers.csv", list(customers[0].keys()), customers)

    # 4. accounts
    accounts = []
    account_id = 1
    customer_accounts = {}  # customer_id -> [account_id,...]
    for c in customers:
        n = random.randint(*N_ACCOUNTS_PER_CUSTOMER)
        customer_accounts[c["customer_id"]] = []
        for _ in range(n):
            open_date = random_date(c["join_date"], date(2026, 9, 1))
            accounts.append({
                "account_id": account_id,
                "customer_id": c["customer_id"],
                "branch_id": random.randint(1, N_BRANCHES),
                "account_type": random.choice(ACCOUNT_TYPES),
                "balance": round(random.uniform(0, 60000), 2),
                "open_date": open_date,
                "status": random.choices(
                    ["activa", "cerrada", "bloqueada"], weights=[85, 10, 5]
                )[0],
            })
            customer_accounts[c["customer_id"]].append(account_id)
            account_id += 1
    write_csv("accounts.csv", list(accounts[0].keys()), accounts)

    # 5. cards
    cards = []
    card_id = 1
    account_by_id = {a["account_id"]: a for a in accounts}
    for c in customers:
        n = random.randint(*N_CARDS_PER_CUSTOMER)
        acc_ids = customer_accounts[c["customer_id"]]
        if not acc_ids:
            continue
        for _ in range(n):
            acc_id = random.choice(acc_ids)
            issue_date = random_date(account_by_id[acc_id]["open_date"], date(2026, 9, 1))
            expiry = issue_date.replace(year=issue_date.year + 4)
            cards.append({
                "card_id": card_id,
                "customer_id": c["customer_id"],
                "account_id": acc_id,
                "card_type": random.choice(CARD_TYPES),
                "issue_date": issue_date,
                "expiry_date": expiry,
                "credit_limit": round(random.uniform(1000, 25000), 2),
                "status": random.choices(["activa", "bloqueada", "vencida"], weights=[85, 10, 5])[0],
            })
            card_id += 1
    write_csv("cards.csv", list(cards[0].keys()), cards)

    # 6. card_transactions (con % en el extranjero)
    card_txns = []
    card_txn_id = 1
    for card in cards:
        n = random.randint(*N_CARD_TXN_PER_CARD)
        start = datetime.combine(card["issue_date"], datetime.min.time())
        end = datetime(2026, 9, 14)
        for _ in range(n):
            is_abroad = random.random() < 0.12  # ~12% transacciones en el extranjero
            category = random.choice(MERCHANT_CATEGORIES)
            if is_abroad:
                category = f"{category} ({random.choice(CIUDADES_EXTRANJERO)})"
            amount = round(random.uniform(10, 3000) * (2.5 if is_abroad else 1), 2)
            is_fraud = random.random() < 0.015  # ~1.5% marcado como fraude
            card_txns.append({
                "card_txn_id": card_txn_id,
                "card_id": card["card_id"],
                "txn_date": start + (end - start) * random.random(),
                "merchant_category": category,
                "amount": amount,
                "is_fraud": is_fraud,
            })
            card_txn_id += 1
    write_csv("card_transactions.csv", list(card_txns[0].keys()), card_txns)

    # 7. transactions (movimientos de cuenta)
    transactions = []
    txn_id = 1
    for acc in accounts:
        n = random.randint(*N_TXN_PER_ACCOUNT)
        start = datetime.combine(acc["open_date"], datetime.min.time())
        end = datetime(2026, 9, 14)
        for _ in range(n):
            transactions.append({
                "transaction_id": txn_id,
                "account_id": acc["account_id"],
                "txn_date": start + (end - start) * random.random(),
                "txn_type": random.choice(TXN_TYPES),
                "amount": round(random.uniform(20, 8000), 2),
                "channel": random.choice(CHANNELS),
                "merchant_category": random.choice(MERCHANT_CATEGORIES),
            })
            txn_id += 1
    write_csv("transactions.csv", list(transactions[0].keys()), transactions)

    # 8. loans
    loans = []
    for i in range(1, N_LOANS + 1):
        cust = random.choice(customers)
        start_date = random_date(cust["join_date"], date(2026, 6, 1))
        loans.append({
            "loan_id": i,
            "customer_id": cust["customer_id"],
            "branch_id": random.randint(1, N_BRANCHES),
            "loan_type": random.choice(LOAN_TYPES),
            "loan_amount": round(random.uniform(2000, 150000), 2),
            "interest_rate": round(random.uniform(8, 35), 2),
            "term_months": random.choice([6, 12, 24, 36, 48, 60]),
            "start_date": start_date,
            "status": random.choices(LOAN_STATUS, weights=[55, 25, 12, 8])[0],
        })
    write_csv("loans.csv", list(loans[0].keys()), loans)

    # 9. loan_payments
    loan_payments = []
    payment_id = 1
    for loan in loans:
        n_payments = random.randint(1, min(loan["term_months"], 24))
        pay_date = loan["start_date"]
        for _ in range(n_payments):
            pay_date = pay_date + timedelta(days=30)
            if pay_date > date(2026, 9, 14):
                break
            principal = round(loan["loan_amount"] / loan["term_months"], 2)
            interest = round(principal * (loan["interest_rate"] / 100) / 12, 2)
            loan_payments.append({
                "payment_id": payment_id,
                "loan_id": loan["loan_id"],
                "payment_date": pay_date,
                "amount_paid": round(principal + interest, 2),
                "principal_component": principal,
                "interest_component": interest,
                "late_payment_flag": random.random() < 0.1,
            })
            payment_id += 1
    write_csv("loan_payments.csv", list(loan_payments[0].keys()), loan_payments)

    # 10. support_tickets
    tickets = []
    for i in range(1, N_TICKETS + 1):
        cust = random.choice(customers)
        opened = random_date(cust["join_date"], date(2026, 9, 1))
        status = random.choices(TICKET_STATUS, weights=[15, 15, 30, 40])[0]
        resolved = None
        satisfaction = None
        if status in ("resuelto", "cerrado"):
            resolved = opened + timedelta(days=random.randint(0, 15))
            satisfaction = random.randint(1, 5)
        tickets.append({
            "ticket_id": i,
            "customer_id": cust["customer_id"],
            "issue_type": random.choice(ISSUE_TYPES),
            "date_opened": opened,
            "date_resolved": resolved if resolved else "",
            "status": status,
            "satisfaction_score": satisfaction if satisfaction else "",
        })
    write_csv("support_tickets.csv", list(tickets[0].keys()), tickets)

    print("\nListo. CSVs generados en ./data/")


if __name__ == "__main__":
    main()
