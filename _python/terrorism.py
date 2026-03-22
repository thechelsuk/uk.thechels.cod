# importing modules
import pathlib
import time
import feedparser
import requests
import helper
from datetime import datetime

URL = "https://www.mi5.gov.uk/UKThreatLevel/UKThreatLevel.xml"


def fetch_terrorism_xml(destination):
    headers = {
        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (compatible; uk.thechels.cod-bot/1.0)",
    }
    last_error = None
    for attempt in range(4):
        try:
            response = requests.get(URL, headers=headers, timeout=30)
            response.raise_for_status()
            content = response.text
            lower = content.lower()
            if "just a moment" in lower and "cloudflare" in lower:
                raise RuntimeError("Cloudflare challenge page returned instead of XML")
            destination.write_text(content)
            return content
        except (requests.RequestException, RuntimeError) as error:
            last_error = error
            if attempt == 3:
                raise
            time.sleep(2)
    raise last_error

# processing
if __name__ == "__main__":
    try:
        root = pathlib.Path(__file__).parent.parent.resolve()
        terror_xml = root / "_data/terrorism.xml"

        try:
            xml_content = fetch_terrorism_xml(terror_xml)
        except Exception as error:
            if terror_xml.exists():
                print(f"Fetch failed, using cached terrorism.xml: {error}")
                xml_content = terror_xml.read_text()
            else:
                raise

        parsed = feedparser.parse(xml_content)
        output = parsed["entries"]
        if not output:
            raise RuntimeError("No entries found in terrorism feed")

        for entry in output:
            level = (f"{entry['title']}")
            update = entry['published']
            update = datetime.strptime(
                update, "%A, %B %d, %Y -  %H:%M").strftime("%Y-%m-%d")
            days_since_update = (datetime.now() -
                                 datetime.strptime(update, "%Y-%m-%d")).days
            desc = entry['summary']

            level_class = level.split()[-1]

        string =  f'### {level_class}\n\n'
        string += f'- {level}\n'
        string += f'- It has been {days_since_update} days since the last change ({update})\n'
        string += f'- Details: {desc}\n'

        f = root / "index.md"
        m = f.open().read()
        c = helper.replace_chunk(m, "threat_marker", string)
        f.open("w").write(c)
        print("threat completed")

    except FileNotFoundError:
        print("File does not exist, unable to proceed")