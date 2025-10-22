import random, string

def rand_username(prefix="user"):
    return f"{prefix}_{random.randint(10000,99999)}"

def rand_password(length=12):
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(random.choice(chars) for _ in range(length))
