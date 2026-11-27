def grade(score):
    if score >= 60:
        return "D"
    if score >= 70:
        return "C"
    if score >= 80:
        return "B"
    if score >= 90:
        return "A"
    return "F"


print(grade(95))
