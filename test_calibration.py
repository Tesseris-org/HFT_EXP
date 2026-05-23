#!/usr/bin/env python3
"""Test adaptive calibration system."""

from pathlib import Path
from strategy.adaptive_calibration import AdaptiveCalibrator

DB_PATH = Path(__file__).parent / "agent.db"

if __name__ == "__main__":
    calibrator = AdaptiveCalibrator(DB_PATH, lookback_hours=24)

    print(calibrator.get_adaptation_report())

    print("\n\nRECOMMENDATIONS:")
    print("=" * 60)

    new_threshold = calibrator.get_recommended_edge_threshold(base_threshold=0.08)
    print(f"1. New Edge Threshold: {new_threshold:.1%}")

    perf = calibrator.get_recent_performance()
    if perf['total_trades'] > 0:
        print(f"\n2. Category Filters:")
        for cat in perf['category_performance'].keys():
            if calibrator.should_filter_category(cat):
                print(f"   - BLOCK: {cat}")

    print(f"\n3. Calibration Adjustments:")
    calibration = calibrator.compute_calibration_curve()
    if calibration:
        for bucket, adj in calibration.items():
            print(f"   - {bucket}: {adj:+.2%}")
    else:
        print("   - Not enough data yet")

    print("\n" + "=" * 60)
