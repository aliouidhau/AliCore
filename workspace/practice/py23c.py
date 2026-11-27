def build_query(keyword, page):
    return "kw=" + keyword + "&page=" + str(page)


print(build_query("底座 A", 2))
