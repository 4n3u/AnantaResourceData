import requests
import os
import yaml

launcher = {
    "cn": {
        "beta": "https://l50.update.netease.com/Ananta_Official_zhCN.yml",
        "live": ""
    },
    "os": {
        "beta": "",
        "live": ""
    }
}

game = {
    "cn": {
        "beta": "https://l50.update.netease.com/version_win_netease_player_online.json",
        "live": ""
    },
    "os": {
        "beta": "",
        "live": ""
    }
}

def get_config_version(url):
    if not url:
        return None, None, None
    try:
        response = requests.get(url)
        data = yaml.safe_load(response.text)
        
        version = data.get("version")
        
        if not version:
            version = data.get("pc", {}).get("pkg", {}).get("showVersion")
            
        return version, response.content, data
    except Exception as e:
        print(f"Error parsing {url}: {e}")
        return None, None, None

def save_file(directory, filename, content):
    if not os.path.exists(directory):
        os.makedirs(directory)
    with open(os.path.join(directory, filename), 'wb') as f:
        f.write(content)

def process_urls(url_dict, base_dir):
    for region, types in url_dict.items():
        for type_, url in types.items():
            if not url:
                continue
            
            version, content, data = get_config_version(url)
            if version:
                filename = url.split('/')[-1]
                directory = f"{base_dir}/{region}/{type_}/{version}"
                save_file(directory, filename, content)
                print(f"Saved {filename} to {directory}")

                if isinstance(data, dict):
                    pkg = data.get("pc", {}).get("pkg", {})
                    base_url = pkg.get("url")
                    patch_id = pkg.get("patch")
                    
                    if base_url and patch_id:
                        if not base_url.endswith('/'):
                            base_url += '/'
                        
                        extra_files = ["catalog.txt", "trunks.txt"]
                        for extra_file in extra_files:
                            file_url = f"{base_url}{patch_id}/{extra_file}"
                            try:
                                print(f"Downloading {extra_file} from {file_url}...")
                                extra_resp = requests.get(file_url)
                                if extra_resp.status_code == 200:
                                    save_file(directory, extra_file, extra_resp.content)
                                    print(f"Saved {extra_file}")
                                else:
                                    print(f"Failed to fetch {extra_file}: Status {extra_resp.status_code}")
                            except Exception as e:
                                print(f"Error downloading {extra_file}: {e}")

            else:
                print(f"Version not found or error for {url}")

if __name__ == "__main__":
    process_urls(launcher, "launcher")
    process_urls(game, "game")
