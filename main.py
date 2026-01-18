import os

from pandas import read_csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from fuzzywuzzy import process

from auto_researcher.scholarly import download_scholar_papers_by_id
from auto_researcher.helpers import git_update_commit_push, connect_nordvpn_uk


def reset():
    connect_nordvpn_uk()
    git_update_commit_push()


def worker(scholar_id, max_downloads):
    print(scholar_id)
    download_scholar_papers_by_id(scholar_id, max_downloads=max_downloads)


def process_batch(batch, max_workers, max_downloads):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, sid, max_downloads) for sid in batch]
        for _ in as_completed(futures):
            pass


def read_researchers(threshold=80):
    df = read_csv("researchers.csv", usecols=["Name", "Google Scholar ID"])

    papers_root = "Papers"
    pulled_researchers = os.listdir(papers_root)

    # build name → folder mapping via fuzzy match
    name_to_folder = {}
    for name in df["Name"]:
        match = process.extractOne(name, pulled_researchers)
        if match and match[1] >= threshold:
            name_to_folder[name] = match[0]
        else:
            raise ValueError(f"No folder match found for researcher: {name}")

    def pdf_count(name):
        folder = name_to_folder[name]
        path = os.path.join(papers_root, folder)
        return sum(f.lower().endswith(".pdf") for f in os.listdir(path))

    df["pdf_count"] = df["Name"].apply(pdf_count)

    # order with lowest count first
    df = df.sort_values("pdf_count", ascending=True).reset_index(drop=True)

    # df = df.sample(frac=1).reset_index(drop=True)

    return df


def main():
    df = read_researchers()

    ids = list(df["Google Scholar ID"].values)

    N = 5  # Number of workers after which to reset
    max_workers = N
    max_max_downloads = 30


    for max_downloads in range(20, max_max_downloads+1):
        for i in range(0, len(ids), N):
            reset()
            batch = ids[i : i + N]
            process_batch(batch, max_workers, max_downloads)


if __name__ == "__main__":
    main()
