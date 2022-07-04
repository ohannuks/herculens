import copy
import jax.numpy as np
from functools import partial
from jax import jit

from herculens.LensImage.Numerics.numerics_subframe import NumericsSubFrame
from herculens.LensImage.image2source_mapping import Image2SourceMapping


__all__ = ['LensImage']


class LensImage(object):
    """Generate lensed images from source light and lens mass/light models."""
    def __init__(self, grid_class, psf_class, 
                 noise_class=None, lens_model_class=None,
                 source_model_class=None, lens_light_model_class=None,
                 kwargs_numerics=None, recompute_model_grids=False):
        """
        :param grid_class: coordinate system, instance of PixelGrid() from herculens.Coordinates.pixel_grid
        :param psf_class: point spread function, instance of PSF() from herculens.Instrument.psf
        :param noise_class: noise properties, instance of Noise() from herculens.Instrument.noise
        :param lens_model_class: lens mass model, instance of LensModel() from herculens.LensModel.lens_model
        :param source_model_class: source light model, instance of LightModel() from herculens.LensModel.lens_model
        :param lens_light_model_class: lens light model, instance of LightModel() from herculens.LensModel.lens_model
        :param kwargs_numerics: keyword arguments for various numerical settings (see .Numerics.numerics_subframe)
        :param recompute_model_grids: if True, recomputes all coordinate grids for pixelated model components
        """
        self.type = 'single-band'
        self.num_bands = 1
        self.PSF = psf_class
        self.Noise = noise_class
        # here we deep-copy the class to prevent issues with model grid creations below
        self.Grid = grid_class
        self.PSF.set_pixel_size(self.Grid.pixel_width)
        if kwargs_numerics is None:
            kwargs_numerics = {}
        self.ImageNumerics = NumericsSubFrame(pixel_grid=self.Grid, psf=self.PSF, **kwargs_numerics)
        if lens_model_class is None:
            from herculens.LensModel.lens_model import LensModel
            lens_model_class = LensModel(lens_model_list=[])
        self.LensModel = lens_model_class
        if self.LensModel.has_pixels:
            self.Grid.create_model_grid(**self.LensModel.pixel_grid_settings, name='lens',
                                        overwrite=recompute_model_grids)
            self.LensModel.set_pixel_grid(self.Grid.model_pixel_axes('lens'))
        self._psf_error_map = self.PSF.psf_error_map_bool
        if source_model_class is None:
            from herculens.LightModel.light_model import LightModel
            source_model_class = LightModel(light_model_list=[])
        self.SourceModel = source_model_class
        if self.SourceModel.has_pixels:
            self.Grid.create_model_grid(**self.SourceModel.pixel_grid_settings, name='source',
                                        overwrite=recompute_model_grids)
            self.SourceModel.set_pixel_grid(self.Grid.model_pixel_axes('source'), self.Grid.pixel_area)
        if lens_light_model_class is None:
            from herculens.LightModel.light_model import LightModel
            lens_light_model_class = LightModel(light_model_list=[])
        self.LensLightModel = lens_light_model_class
        if self.LensLightModel.has_pixels:
            self.Grid.create_model_grid(**self.LensLightModel.pixel_grid_settings, name='lens_light',
                                        overwrite=recompute_model_grids)
            self.LensLightModel.set_pixel_grid(self.Grid.model_pixel_axes('lens_light'), self.Grid.pixel_area)
        self._kwargs_numerics = kwargs_numerics
        self.source_mapping = Image2SourceMapping(lens_model_class, source_model_class)

    def update_psf(self, psf_class):
        """

        update the instance of the class with a new instance of PSF() with a potentially different point spread function

        :param psf_class:
        :return: no return. Class is updated.
        """
        self.PSF = psf_class
        self.PSF.set_pixel_size(self.Grid.pixel_width)
        self.ImageNumerics = NumericsSubFrame(pixel_grid=self.Grid, psf=self.PSF, **self._kwargs_numerics)
    
    def source_surface_brightness(self, kwargs_source, kwargs_lens=None,
                                  unconvolved=False, de_lensed=False, k=None, k_lens=None):
        """

        computes the source surface brightness distribution

        :param kwargs_source: list of keyword arguments corresponding to the superposition of different source light profiles
        :param kwargs_lens: list of keyword arguments corresponding to the superposition of different lens profiles
        :param kwargs_extinction: list of keyword arguments of extinction model
        :param unconvolved: if True: returns the unconvolved light distribution (prefect seeing)
        :param de_lensed: if True: returns the un-lensed source surface brightness profile, otherwise the lensed.
        :param k: list of bool or list of int to select which source profiles to include
        :param k_lens: list of bool or list of int to select which lens mass profiles to include
        :return: 2d array of surface brightness pixels
        """
        if len(self.SourceModel.profile_type_list) == 0:
            return np.zeros((self.Grid.num_pixel_axes))
        ra_grid, dec_grid = self.ImageNumerics.coordinates_evaluate
        if de_lensed is True:
            source_light = self.SourceModel.surface_brightness(ra_grid, dec_grid, kwargs_source, k=k)
        else:
            source_light = self.source_mapping.image_flux_joint(ra_grid, dec_grid, kwargs_lens, kwargs_source, k=k, k_lens=k_lens)
        source_light_final = self.ImageNumerics.re_size_convolve(source_light, unconvolved=unconvolved)
        return source_light_final

    def lens_surface_brightness(self, kwargs_lens_light, unconvolved=False, k=None):
        """

        computes the lens surface brightness distribution

        :param kwargs_lens_light: list of keyword arguments corresponding to different lens light surface brightness profiles
        :param unconvolved: if True, returns unconvolved surface brightness (perfect seeing), otherwise convolved with PSF kernel
        :param k: list of bool or list of int to select which model profiles to include
        :return: 2d array of surface brightness pixels
        """
        ra_grid, dec_grid = self.ImageNumerics.coordinates_evaluate
        lens_light = self.LensLightModel.surface_brightness(ra_grid, dec_grid, kwargs_lens_light, k=k)
        lens_light_final = self.ImageNumerics.re_size_convolve(lens_light, unconvolved=unconvolved)
        return lens_light_final

    @partial(jit, static_argnums=(0, 4, 5, 6, 7, 8, 9))
    def model(self, kwargs_lens=None, kwargs_source=None,
              kwargs_lens_light=None, unconvolved=False, source_add=True,
              lens_light_add=True, k_lens=None, k_source=None, k_lens_light=None):
        """
        Create the 2D model image from parameter values.
        Note: due to JIT compilation, the first call to this method will be slower.

        :param kwargs_lens: list of keyword arguments corresponding to the superposition of different lens profiles
        :param kwargs_source: list of keyword arguments corresponding to the superposition of different source light profiles
        :param kwargs_lens_light: list of keyword arguments corresponding to different lens light surface brightness profiles
        :param kwargs_ps: keyword arguments corresponding to "other" parameters, such as external shear and point source image positions
        :param unconvolved: if True: returns the unconvolved light distribution (prefect seeing)
        :param source_add: if True, compute source, otherwise without
        :param lens_light_add: if True, compute lens light, otherwise without
        :param k_lens: list of bool or list of int to select which lens mass profiles to include
        :param k_source: list of bool or list of int to select which source profiles to include
        :param k_lens_light: list of bool or list of int to select which lens light profiles to include
        :return: 2d array of surface brightness pixels of the simulation
        """
        model = np.zeros((self.Grid.num_pixel_axes))
        if source_add is True:
            model += self.source_surface_brightness(kwargs_source, kwargs_lens, unconvolved=unconvolved,
                                                    k=k_source, k_lens=k_lens)
        if lens_light_add is True:
            model += self.lens_surface_brightness(kwargs_lens_light, unconvolved=unconvolved, k=k_lens_light)
        return model

    def simulation(self, add_poisson=True, add_gaussian=True, 
                   compute_true_noise_map=True, noise_seed=18, 
                   **model_kwargs):
        """
        same as model() but with noise added

        :param compute_true_noise_map: if True (default), define the noise map (diagonal covariance matrix)
        to be the 'true' one, i.e. based on the noiseless model image.
        :param noise_seed: the seed that will be used by the PRNG from JAX to fix the noise realization.
        The default is the arbtrary value 18, so it is the user task to change it for different realizations.
        """
        if self.Noise is None:
            raise ValueError("Impossible to generate noise realisation because no noise class has been set")
        model = self.model(**model_kwargs)
        noise = self.Noise.realisation(model, noise_seed, add_poisson=add_poisson, add_gaussian=add_gaussian)
        simu = model + noise
        self.Noise.set_data(simu)
        if compute_true_noise_map is True:
            self.Noise.compute_noise_map_from_model(model)
        return simu

    def normalized_residuals(self, data, model, mask=None):
        """
        compute the map of normalized residuals, 
        given the data and the model image
        """
        if mask is None:
            mask = np.ones(self.Grid.num_pixel_axes)
        noise_var = self.Noise.C_D_model(model)
        # noise_var = self.Noise.C_D
        norm_res = (model - data) / np.sqrt(noise_var) * mask
        return norm_res

    def reduced_chi2(self, data, model, mask=None):
        """
        compute the reduced chi2 of the data given the model
        """
        if mask is None:
            mask = np.ones(self.Grid.num_pixel_axes)
        norm_res = self.normalized_residuals(data, model, mask=mask)
        num_data_points = np.sum(mask)
        return np.sum(norm_res**2) / num_data_points
        