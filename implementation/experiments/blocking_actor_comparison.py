"""Offline comparison of the two originally blocking construction actors."""
import argparse
from pathlib import Path
import critical_actor_comparison as comparison


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--review',type=Path,required=True)
    p.add_argument('--dataset',type=Path,default=Path('/data/nuscenes_dataset'))
    a=p.parse_args()
    comparison.CASES={'construction_yield':(3,['left_curb_pedestrian','right_worker'])}
    comparison.evaluate(a.out,a.review,a.dataset)
