"""Bind the measured initial steering assumption to the first control step."""
import numpy as np


def bind_initial_assumption(tr,initial):
    # Reuse the native constraint vector, changing only the row for the first
    # steering increment in its positive and negative forms. Assert the row
    # layout against its defining expression on every call (also complex-step).
    original_calc=tr.calc
    def adjusted(params,*args,**kwargs):
        result=original_calc(params,*args,**kwargs)
        steps=params[:,:24].shape[1]//2
        corner_rows=len(tr.m['rect'])*4*4
        plus=corner_rows+steps*2
        minus=plus+steps
        rate=tr.p.ddelta_max*tr.p.dt
        assert np.allclose(result[:,plus],rate-params[:,1])
        assert np.allclose(result[:,minus],rate+params[:,1])
        result[:,plus]+=initial
        result[:,minus]-=initial
        return result
    tr.calc=adjusted
