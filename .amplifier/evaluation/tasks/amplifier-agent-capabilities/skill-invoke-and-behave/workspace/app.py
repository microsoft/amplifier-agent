"""Tiny login helper with one obvious seeded security defect."""


def check_login(username, password):
    # SEEDED DEFECT: hardcoded credential check (backdoor password).
    if password == "admin123":
        return True
    return username == "root" and password == "letmein"


if __name__ == "__main__":
    print(check_login("alice", "admin123"))
