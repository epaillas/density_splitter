import numpy as np
import matplotlib.pyplot as plt
from rsdmodel import Model1, Model2
import os
import sys
import argparse
import emcee
from multiprocessing import Pool

def log_probability(theta):
        lp = model.log_prior(theta)
        if not np.isfinite(lp):
            return -np.inf
        return lp + model.log_likelihood(theta)

parser = argparse.ArgumentParser(description='MCMC for the void-galaxy correlation function.')

parser.add_argument('--ncores', type=int)
parser.add_argument('--xi_smu', type=str)
parser.add_argument('--xi_r', type=str)
parser.add_argument('--delta_r', type=str)
parser.add_argument('--sv_r', type=str)
parser.add_argument('--covmat', type=str)
parser.add_argument('--full_fit', type=int)
parser.add_argument('--smin', type=float)
parser.add_argument('--smax', type=float)
parser.add_argument('--model', type=int)
parser.add_argument('--backend_name', type=str)

args = parser.parse_args()  

os.environ["OMP_NUM_THREADS"] = "1"

if args.model == 1:
    model = Model1(delta_r_file=args.delta_r, xi_r_file=args.xi_r, sv_file=args.sv_r,
                    xi_smu_file=args.xi_smu, covmat_file=args.covmat,
                    full_fit=args.full_fit, smin=args.smin, smax=args.smax)

    ndim = 3
    nwalkers = 28
    niter = 10000

    fs8 = 0.472
    sigma_v = 360
    epsilon = 1.0

    start_params = np.array([fs8, sigma_v, epsilon])
    scales = [1, 1000, 1]

    p0 = [start_params + 1e-2 * np.random.randn(ndim) * scales for i in range(nwalkers)]

    print('Running emcee with the following parameters:')
    print('nwalkers: ' + str(nwalkers))
    print('ndim: ' + str(ndim))
    print('niter: ' + str(niter))
    print('backend: ' + args.backend_name)
    print('Running in {} CPUs'.format(args.ncores))

    backend = emcee.backends.HDFBackend(args.backend_name)
    backend.reset(nwalkers, ndim)

    with Pool(processes=args.ncores) as pool:

        sampler = emcee.EnsembleSampler(nwalkers, ndim,
                                        log_probability,
                                        backend=backend,
                                        pool=pool)
        sampler.run_mcmc(p0, niter, progress=True)


if args.model == 2:
    model = Model2(delta_r_file=args.delta_r, xi_r_file=args.xi_r,
                    xi_smu_file=args.xi_smu, covmat_file=args.covmat,
                    full_fit=args.full_fit, smin=args.smin, smax=args.smax)

    ndim = 2
    nwalkers = 32
    niter = 5000

    fs8 = 0.472
    epsilon = 1.0

    start_params = np.array([fs8, epsilon])
    scales = [1, 1]

    p0 = [start_params + 1e-2 * np.random.randn(ndim) * scales for i in range(nwalkers)]

    print('Running emcee with the following parameters:')
    print('nwalkers: ' + str(nwalkers))
    print('ndim: ' + str(ndim))
    print('niter: ' + str(niter))
    print('backend: ' + args.backend_name)
    print('Running in {} CPUs'.format(args.ncores))

    backend = emcee.backends.HDFBackend(args.backend_name)
    backend.reset(nwalkers, ndim)

    with Pool(processes=args.ncores) as pool:

        sampler = emcee.EnsembleSampler(nwalkers, ndim,
                                        log_probability,
                                        backend=backend,
                                        pool=pool)
        sampler.run_mcmc(p0, niter, progress=True)

