import app

s = "bb4482066546c25123bd54db3abc05e7"
h1 = app.hash_password("admin123", s)
print("H1:", h1)
h2 = app.hash_password("admin123", s)
print("H2:", h2)
print("Equal?", h1 == h2)
