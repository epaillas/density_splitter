
import numpy as np
import sys
import os
from astropy.io import fits
from cosmology import Cosmology
from scipy.integrate import quad, simps
from scipy.interpolate import RectBivariateSpline, InterpolatedUnivariateSpline, interp1d
from scipy.optimize import fsolve
from scipy.signal import savgol_filter

class RSDModel:
    '''
    Nadathur & Percival RSD model (full)
    '''

    def __init__(self,
                 delta_r_file,
                 xi_r_file,
                 xi_smu_file,
                 covmat_file,
                 sv_file=None,
                 vr_file=None,
                 full_fit=1,
                 smin=0,
                 smax=100,
                 model=1,
                 const_sv=False):

        self.delta_r_file = delta_r_file
        self.xi_r_file = xi_r_file
        self.sv_file = sv_file
        self.vr_file = vr_file
        self.xi_smu_file = xi_smu_file
        self.covmat_file = covmat_file
        self.smin = smin
        self.smax = smax
        self.const_sv = const_sv
        self.model = model

        # full fit (monopole + quadrupole)
        self.full_fit = bool(full_fit)

        print("Setting up redshift-space distortions Model 1.")

        # cosmology for Minerva
        self.om_m = 0.285
        self.s8 = 0.828
        self.cosmo = Cosmology(om_m=self.om_m, s8=self.s8)
        self.nmocks = 300

        self.eff_z = 0.57
        self.b = 2.01

        self.growth = self.cosmo.get_growth(self.eff_z)
        self.f = self.cosmo.get_f(self.eff_z)
        self.s8norm = self.s8 * self.growth 

        eofz = np.sqrt((self.om_m * (1 + self.eff_z) ** 3 + 1 - self.om_m))
        self.iaH = (1 + self.eff_z) / (100. * eofz) 

        # read covariance matrix
        if os.path.isfile(self.covmat_file):
            print('Reading covariance matrix: ' + self.covmat_file)
            self.cov = np.load(self.covmat_file)
            self.icov = np.linalg.inv(self.cov)
        else:
            sys.exit('Covariance matrix not found.')

        # read real-space monopole
        data = np.genfromtxt(self.xi_r_file)
        self.r_for_xi = data[:,0]
        xi_r = data[:,-2]
        self.xi_r = InterpolatedUnivariateSpline(self.r_for_xi, xi_r, k=3, ext=3)

        # read void-matter correlation function
        data = np.genfromtxt(self.delta_r_file)
        self.r_for_delta = data[:,0]
        delta_r = data[:,-2]
        self.delta_r = InterpolatedUnivariateSpline(self.r_for_delta, delta_r, k=3, ext=3)

        integral = np.zeros_like(self.r_for_delta)
        for i in range(len(integral)):
            integral[i] = quad(lambda x: self.delta_r(x) * x ** 2, 0, self.r_for_delta[i], full_output=1)[0]
        Delta_r = 3 * integral / self.r_for_delta ** 3
        self.Delta_r = InterpolatedUnivariateSpline(self.r_for_delta, Delta_r, k=3, ext=3)

        if self.model == 1 or self.model == 3:
            # read los velocity dispersion profile
            data = np.genfromtxt(self.sv_file)
            self.r_for_sv = data[:,0]
            if self.const_sv:
                sv = np.ones(len(self.r_for_sv))
            else:
                sv = data[:,-2] / data[-1, -2]
                sv = savgol_filter(sv, 3, 1)
            self.sv = InterpolatedUnivariateSpline(self.r_for_sv, sv, k=3, ext=3)

        if self.model == 3:
            # read radial velocity profile
            data = np.genfromtxt(self.vr_file)
            self.r_for_vr = data[:,0]
            vr = data[:,-2]
            dvr = np.gradient(vr, self.r_for_vr)
            self.vr = InterpolatedUnivariateSpline(self.r_for_vr, vr, k=3, ext=3)
            self.dvr = InterpolatedUnivariateSpline(self.r_for_vr, dvr, k=3, ext=3)

        # read redshift-space correlation function
        self.s_for_xi, self.mu_for_xi, xi_smu_obs = self.readCorrFile(self.xi_smu_file)
        s, self.xi0_s = self._getMonopole(self.s_for_xi, self.mu_for_xi, xi_smu_obs)
        s, self.xi2_s = self._getQuadrupole(self.s_for_xi, self.mu_for_xi, xi_smu_obs)

        # restrict measured vectors to the desired fitting scales
        idx = (s >= self.smin) & (s <= self.smax)

        self.s_for_xi = self.s_for_xi[idx]
        self.r_for_xi = self.r_for_xi[idx]
        self.r_for_delta = self.r_for_delta[idx]
        self.r_for_sv = self.r_for_sv[idx]
        self.xi0_s = self.xi0_s[idx]
        self.xi2_s = self.xi2_s[idx]


        if self.full_fit:
            self.datavec = np.concatenate((self.xi0_s, self.xi2_s))
        else:
            self.datavec = self.xi2_s
        

    def log_likelihood(self, theta):
        if self.model == 1:
            fs8, sigma_v, epsilon = theta
        else:
            fs8, epsilon = theta

        alpha = 1.0
        alpha_para = alpha * epsilon ** (-2/3)
        alpha_perp = epsilon * alpha_para

        if self.model == 1 or self.model == 3:
            xi0, xi2 = self.model1_theory(fs8,
                                          sigma_v,
                                          alpha_perp,
                                          alpha_para,
                                          self.s_for_xi,
                                          self.mu_for_xi)
        else:
            xi0, xi2 = self.model2_theory(fs8,
                                          alpha_perp,
                                          alpha_para,
                                          self.s_for_xi,
                                          self.mu_for_xi)

        if self.full_fit:
            modelvec = np.concatenate((xi0, xi2))
        else:
            modelvec = xi2

        chi2 = np.dot(np.dot((modelvec - self.datavec), self.icov), modelvec - self.datavec)
        loglike = -self.nmocks/2 * np.log(1 + chi2/(self.nmocks-1))
        return loglike

    def log_prior(self, theta):
        if self.model == 1 or self.model == 3:
            fs8, sigma_v, epsilon = theta
            if 0.1 < fs8 < 2.0 and 1 < sigma_v < 500 and 0.8 < epsilon < 1.2:
                return 0.0
        else:
            fs8, epsilon = theta
            if 0.1 < fs8 < 2.0 and 0.8 < epsilon < 1.2:
                return 0.0
        
        return -np.inf


    def model1_theory(self, fs8, sigma_v, alpha_perp, alpha_para, s, mu):

        monopole = np.zeros(len(s))
        quadrupole = np.zeros(len(s))
        true_mu = np.zeros(len(mu))
        xi_model = np.zeros(len(mu))
        scaled_fs8 = fs8 / self.s8norm

        # rescale input monopole functions to account for alpha values
        mus = np.linspace(0, 1., 100)
        r = self.r_for_delta
        rescaled_r = np.zeros_like(r)
        for i in range(len(r)):
            rescaled_r[i] = np.trapz((r[i] * alpha_para) * np.sqrt(1. + (1. - mus ** 2) *
                            (alpha_perp ** 2 / alpha_para ** 2 - 1)), mus)

        x = rescaled_r
        y1 = self.xi_r(r)
        y2 = self.delta_r(r)
        y3 = self.Delta_r(r)
        y4 = self.sv(r)

        # build rescaled interpolating functions using the relabelled separation vectors
        rescaled_xi_r = InterpolatedUnivariateSpline(x, y1, k=3)
        rescaled_delta_r = InterpolatedUnivariateSpline(x, y2, k=3, ext=3)
        rescaled_Delta_r = InterpolatedUnivariateSpline(x, y3, k=3, ext=3)
        rescaled_sv = InterpolatedUnivariateSpline(x, y4, k=3, ext=3)
        sigma_v = alpha_para * sigma_v
 

        for i in range(len(s)):
            for j in range(len(mu)):
                true_sperp = s[i] * np.sqrt(1 - mu[j] ** 2) * alpha_perp
                true_spar = s[i] * mu[j] * alpha_para
                true_s = np.sqrt(true_spar ** 2. + true_sperp ** 2.)
                true_mu[j] = true_spar / true_s

                rpar = true_spar + true_s * scaled_fs8 * rescaled_Delta_r(true_s) * true_mu[j] / 3.
                sy_central = sigma_v * rescaled_sv(np.sqrt(true_sperp**2 + rpar**2)) * self.iaH
                y = np.linspace(-3 * sy_central, 3 * sy_central, 100)

                rpary = rpar - y
                rr = np.sqrt(true_sperp ** 2 + rpary ** 2)
                sy = sigma_v * rescaled_sv(rr) * self.iaH

                integrand = (1 + rescaled_xi_r(rr)) * \
                            (1 + (scaled_fs8 * rescaled_Delta_r(rr) / 3. - y * true_mu[j] / rr) * (1 - true_mu[j]**2) +
                             scaled_fs8 * (rescaled_delta_r(rr) - 2 * rescaled_Delta_r(rr) / 3.) * true_mu[j]**2)
                integrand = integrand * np.exp(-(y**2) / (2 * sy_central**2)) / (np.sqrt(2 * np.pi) * sy_central)
                xi_model[j] = np.trapz(integrand, y) - 1


            # build interpolating function for xi_smu at true_mu
            mufunc = InterpolatedUnivariateSpline(true_mu[np.argsort(true_mu)], xi_model[np.argsort(true_mu)], k=3)

            # get multipoles
            xaxis = np.linspace(-1, 1, 1000)

            yaxis = mufunc(xaxis) / 2
            monopole[i] = simps(yaxis, xaxis)

            yaxis = mufunc(xaxis) * 5 / 2 * (3 * xaxis**2 - 1) / 2
            quadrupole[i] = simps(yaxis, xaxis)
            
            
        return monopole, quadrupole

    def model2_theory(self, fs8, alpha_perp, alpha_para, s, mu):

        monopole = np.zeros(len(s))
        quadrupole = np.zeros(len(s))
        true_mu = np.zeros(len(mu))
        xi_model = np.zeros(len(mu))
        scaled_fs8 = fs8 / self.s8norm

        # rescale input monopole functions to account for alpha values
        mus = np.linspace(0, 1., 100)
        r = self.r_for_delta
        rescaled_r = np.zeros_like(r)
        for i in range(len(r)):
            rescaled_r[i] = np.trapz((r[i] * alpha_para) * np.sqrt(1. + (1. - mus ** 2) *
                            (alpha_perp ** 2 / alpha_para ** 2 - 1)), mus)

        x = rescaled_r
        y1 = self.xi_r(r)
        y2 = self.delta_r(r)
        y3 = self.Delta_r(r)

        # build rescaled interpolating functions using the relabelled separation vectors
        rescaled_xi_r = InterpolatedUnivariateSpline(x, y1, k=3)
        rescaled_delta_r = InterpolatedUnivariateSpline(x, y2, k=3, ext=3)
        rescaled_Delta_r = InterpolatedUnivariateSpline(x, y3, k=3, ext=3)

        for i in range(len(s)):
            for j in range(len(mu)):
                true_sperp = s[i] * np.sqrt(1 - mu[j] ** 2) * alpha_perp
                true_spar = s[i] * mu[j] * alpha_para
                true_s = np.sqrt(true_spar ** 2. + true_sperp ** 2.)
                true_mu[j] = true_spar / true_s

                r = true_s * (1 + scaled_fs8/3 * rescaled_Delta_r(true_s) * true_mu[j]**2)

                xi_model[j] = rescaled_xi_r(r) + scaled_fs8/3 * rescaled_Delta_r(r) *\
                            (1 + rescaled_xi_r(r)) + scaled_fs8 * true_mu[j]**2 *\
                            (rescaled_delta_r(r) - rescaled_Delta_r(r)) * \
                            (1 + rescaled_xi_r(r))


            # build interpolating function for xi_smu at true_mu
            mufunc = InterpolatedUnivariateSpline(true_mu[np.argsort(true_mu)], xi_model[np.argsort(true_mu)], k=3)

            # get multipoles
            xaxis = np.linspace(-1, 1, 1000)

            yaxis = mufunc(xaxis) / 2
            monopole[i] = simps(yaxis, xaxis)

            yaxis = mufunc(xaxis) * 5 / 2 * (3 * xaxis**2 - 1) / 2
            quadrupole[i] = simps(yaxis, xaxis)
            
        return monopole, quadrupole

    def model3_theory(self, fs8, sigma_v, alpha_perp, alpha_para, s, mu):

        monopole = np.zeros(len(s))
        quadrupole = np.zeros(len(s))
        true_mu = np.zeros(len(mu))
        xi_model = np.zeros(len(mu))
        scaled_fs8 = fs8 / self.s8norm

        # rescale input monopole functions to account for alpha values
        mus = np.linspace(0, 1., 100)
        r = self.r_for_delta
        rescaled_r = np.zeros_like(r)
        for i in range(len(r)):
            rescaled_r[i] = np.trapz((r[i] * alpha_para) * np.sqrt(1. + (1. - mus ** 2) *
                            (alpha_perp ** 2 / alpha_para ** 2 - 1)), mus)

        x = rescaled_r
        y1 = self.xi_r(r)
        y2 = self.delta_r(r)
        y3 = self.Delta_r(r)
        y4 = self.vr(r)
        y5 = self.dvr(r)
        y6 = self.sv(r)

        # build rescaled interpolating functions using the relabelled separation vectors
        rescaled_xi_r = InterpolatedUnivariateSpline(x, y1, k=3)
        rescaled_delta_r = InterpolatedUnivariateSpline(x, y2, k=3, ext=3)
        rescaled_Delta_r = InterpolatedUnivariateSpline(x, y3, k=3, ext=3)
        rescaled_vr = InterpolatedUnivariateSpline(x, y4, k=3, ext=3)
        rescaled_dvr = InterpolatedUnivariateSpline(x, y5, k=3, ext=3)
        rescaled_sv = InterpolatedUnivariateSpline(x, y6, k=3, ext=3)
        sigma_v = alpha_para * sigma_v
 

        for i in range(len(s)):
            for j in range(len(mu)):
                true_sperp = s[i] * np.sqrt(1 - mu[j] ** 2) * alpha_perp
                true_spar = s[i] * mu[j] * alpha_para
                true_s = np.sqrt(true_spar ** 2. + true_sperp ** 2.)
                true_mu[j] = true_spar / true_s

                def residual(rpar):
                    rperp = true_sperp
                    r = np.sqrt(rpar**2 + rperp**2)
                    mu = rpar / r
                    res = rpar - true_spar + rescaled_vr(r)*mu**2 * self.iaH
                    return res

                rpar = fsolve(func=residual, x0=true_spar)[0]

                sy_central = sigma_v * rescaled_sv(np.sqrt(true_sperp**2 + rpar**2)) * self.iaH
                y = np.linspace(-5 * sy_central, 5 * sy_central, 100)

                rpar = fsolve(func=residual, x0=true_spar)[0] - y
                rr = np.sqrt(true_sperp ** 2 + rpar ** 2)
                sy = sigma_v * rescaled_sv(rr) * self.iaH


                # integrand = (1 + rescaled_xi_r(rr)) * \
                #             (1 + (scaled_fs8 * rescaled_Delta_r(rr) / 3. - y * true_mu[j] / rr) * (1 - true_mu[j]**2) +
                #              scaled_fs8 * (rescaled_delta_r(rr) - 2 * rescaled_Delta_r(rr) / 3.) * true_mu[j]**2)
                # integrand = integrand * np.exp(-(y**2) / (2 * sy**2)) / (np.sqrt(2 * np.pi) * sy)
                # xi_model[j] = np.trapz(integrand, y) - 1

                integrand = (1 + rescaled_xi_r(rr)) * (1 + rescaled_vr(rr)/(rr/self.iaH) +\
                                                  (rescaled_dvr(rr) - rescaled_vr(rr)/rr)*self.iaH * true_mu[j]**2)**(-1)

                integrand = integrand * np.exp(-(y**2) / (2 * sy**2)) / (np.sqrt(2 * np.pi) * sy)

                xi_model[j] = np.trapz(integrand, y) - 1

                # xi_model[j] = (1 + rescaled_xi_r(rr)) * (1 + rescaled_vr(rr)/(rr/self.iaH) +\
                #                                   (rescaled_dvr(rr) - rescaled_vr(rr)/rr)*self.iaH * true_mu[j]**2)**(-1) - 1 


            # build interpolating function for xi_smu at true_mu
            mufunc = InterpolatedUnivariateSpline(true_mu, xi_model, k=3)
            
            # get multipoles
            xaxis = np.linspace(-1, 1, 1000)

            yaxis = mufunc(xaxis) / 2
            monopole[i] = simps(yaxis, xaxis)

            yaxis = mufunc(xaxis) * 5 / 2 * (3 * xaxis**2 - 1) / 2
            quadrupole[i] = simps(yaxis, xaxis)
            
            
        return monopole, quadrupole

    
    def readCorrFile(self, fname):
        data = np.genfromtxt(fname)
        s = np.unique(data[:,0])
        mu = np.unique(data[:,1])

        xi_smu = np.zeros([len(s), len(mu)])
        counter = 0
        for i in range(len(mu)):
            for j in range(len(s)):
                xi_smu[j, i] = data[counter, -2]
                counter += 1

        return s, mu, xi_smu


    def _getMonopole(self, s, mu, xi_smu):
        monopole = np.zeros(xi_smu.shape[0])
        for i in range(xi_smu.shape[0]):
            mufunc = InterpolatedUnivariateSpline(mu, xi_smu[i, :], k=3)
            xaxis = np.linspace(-1, 1, 1000)
            yaxis = mufunc(xaxis) / 2
            monopole[i] = simps(yaxis, xaxis)
        return s, monopole

    def _getQuadrupole(self, s, mu, xi_smu):
        quadrupole = np.zeros(xi_smu.shape[0])
        for i in range(xi_smu.shape[0]):
            mufunc = InterpolatedUnivariateSpline(mu, xi_smu[i, :], k=3)
            xaxis = np.linspace(-1, 1, 1000)
            yaxis = mufunc(xaxis) * 5 / 2 * (3 * xaxis**2 - 1) / 2
            quadrupole[i] = simps(yaxis, xaxis)

        return s, quadrupole

