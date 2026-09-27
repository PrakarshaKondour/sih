#!/usr/bin/env python3
"""
Run this FIRST on a machine with real internet access (spec section 15,
step 1-3): prints the actual layer names/fields/geometry types on TGRAC's
public MapServers, so you can fix any mismatch between
backend/app/ingestion/tgrac_client.py's assumed field names and what the
live service actually returns, before trusting its output.

Usage:
    cd backend && python ../scripts/probe_tgrac_layers.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ingestion.tgrac_client import (
    TGRACClient, TCUR_MAPSERVER, GHMC_NALAS_MAPSERVER, MUSI_BASIN_MAPSERVER,
)

MAPSERVERS = {
    "TCUR (manholes/sewer/roads)": TCUR_MAPSERVER,
    "GHMC Nalas": GHMC_NALAS_MAPSERVER,
    "Musi Basin": MUSI_BASIN_MAPSERVER,
}


def main():
    client = TGRACClient()
    for label, url in MAPSERVERS.items():
        print(f"\n=== {label} ===\n{url}")
        try:
            layers = client.list_layers(url)
        except Exception as e:
            print(f"  FAILED to reach this MapServer: {e}")
            continue
        for layer in layers:
            print(f"  [{layer['id']}] {layer['name']}  (geometry={layer['geometryType']})")
            try:
                sample = client.get_layer(url, layer_id=layer["id"],
                                            where="1=1")
                if len(sample) > 0:
                    print(f"      fields: {[c for c in sample.columns if c != 'geometry']}")
                    print(f"      feature count (unbounded query): {len(sample)}")
            except Exception as e:
                print(f"      could not sample this layer: {e}")


if __name__ == "__main__":
    main()
