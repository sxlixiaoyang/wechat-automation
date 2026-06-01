from url_fetcher import fetch_url

url = "https://github.com/CookSleep/gpt_image_playground/issues/82"
content = fetch_url(url)
print(content)