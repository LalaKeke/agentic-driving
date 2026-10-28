"""Offline-only one-actor bus extension of the fixed critical-object report."""
import argparse
from pathlib import Path
import critical_actor_comparison as comparison


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--review',type=Path)
    parser.add_argument('--dataset',type=Path,default=Path('/data/nuscenes_dataset'))
    args=parser.parse_args()
    comparison.BASE=comparison.ROOT/'implementation/results'
    comparison.CASES={'moving_prototype_2s_20261028_v1':(2,['curbside_bus'])}
    if args.review:comparison.evaluate(args.out,args.review,args.dataset)
    else:comparison.prepare(args.out,args.dataset)


if __name__=='__main__':main()
