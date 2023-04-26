import os

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv("../.env")

engine = create_engine(
    f"postgresql+psycopg://{os.environ.get('DATABASE_USERNAME')}:{os.environ.get('DATABASE_PASSWORD')}@localhost:5432/{os.environ.get('DATABASE_NAME')}"
)


def download_image(phid):
    url = f"https://www.aph.gov.au/api/parliamentarian/{phid}/image"

    r = requests.get(url)
    if r.ok:
        with open(f"../app/assets/images/{phid}.jpg", "wb") as f:
            f.write(r.content)
    else:
        print(f"Error downloading {url} - {r.status_code}")


if __name__ == "__main__":
    pd.read_sql(
        'SELECT DISTINCT "Image", mp_id FROM member_aph_46  WHERE "Image" IS NOT NULL',
        engine,
    ).apply(lambda x: download_image(x["mp_id"]), axis=1)
