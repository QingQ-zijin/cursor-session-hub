"""Discard only this product's reproducible installers restored with Rust cache."""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--target',required=True,choices=['x86_64-pc-windows-msvc','aarch64-apple-darwin','x86_64-apple-darwin'])
    args=parser.parse_args()
    product=json.loads((ROOT/'desktop/tauri.conf.json').read_text())['productName']
    target_root=(ROOT/'desktop/target').resolve()
    bundle=(target_root/args.target/'release/bundle').resolve()
    if not bundle.is_relative_to(target_root):raise ValueError('Installer directory escaped build workspace')
    removed=[]
    for subfolder,extension in [('nsis','exe'),('dmg','dmg')]:
        for item in (bundle/subfolder).glob(product+'_*.'+extension):
            if not item.resolve().is_relative_to(bundle):raise ValueError('Installer escaped product output directory')
            item.unlink()
            removed.append(item.name)
    print('Removed cached installer outputs:',removed)

if __name__=='__main__':main()
