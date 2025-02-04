# Describes a mass model, as a list of mass profiles
# 
# Copyright (c) 2021, herculens developers and contributors
# Copyright (c) 2018, Simon Birrer & lenstronomy contributors
# based on the LensModel module from lenstronomy (version 1.9.3)

__author__ = 'sibirrer', 'austinpeel', 'aymgal'


from herculens.MassModel.Profiles import (gaussian_potential, point_mass, multipole,
                                           shear, sie, sis, nie, epl, pixelated)
from herculens.Util import util

__all__ = ['MassModelBase']

SUPPORTED_MODELS = [
    'EPL', 'NIE', 'SIE', 'SIS', 'GAUSSIAN', 'POINT_MASS', 
    'SHEAR', 'SHEAR_GAMMA_PSI', 'MULTIPOLE',
    'PIXELATED', 'PIXELATED_DIRAC',
]


# TODO: create parent for methods shared between MassProfileBase and LightProfileBase


class MassModelBase(object):
    """Base class for managing lens models in single- or multi-plane lensing."""
    def __init__(self, lens_model_list, 
                 kwargs_pixelated=None, 
                 no_complex_numbers=False,
                 pixel_interpol='fast_bilinear', 
                 pixel_derivative_type='interpol',
                 kwargs_pixel_grid_fixed=None):
        """Create a MassProfileBase object.

        Parameters
        ----------
        lens_model_list : list of str or class (or a mix of both)
            Lens model profile types or classes.

        """
        self.func_list, self._pix_idx = self._load_model_instances(
            lens_model_list, pixel_derivative_type, pixel_interpol, 
            no_complex_numbers, kwargs_pixel_grid_fixed
        )
        self._num_func = len(self.func_list)
        self._model_list = self.create_model_list(lens_model_list)
        if kwargs_pixelated is None:
            kwargs_pixelated = {}
        self._kwargs_pixelated = kwargs_pixelated
        
    def create_model_list(self, lens_model_list):
        for i in range(len(lens_model_list)):
            if isinstance(lens_model_list[i], type):
                lens_model_list[i] = lens_model_list[i].__name__
            elif isinstance(lens_model_list[i], str):
                pass
            else:
                raise ValueError("lens_model_list must be a list of strings or classes")
        return lens_model_list

    def _load_model_instances(self, 
            lens_model_list, pixel_derivative_type, pixel_interpol, 
            no_complex_numbers, kwargs_pixel_grid_fixed,
        ):
        func_list = []
        imported_classes = {}
        pix_idx = None
        for idx, lens_type in enumerate(lens_model_list):
            # These models require a new instance per profile as certain pre-computations
            # are relevant per individual profile
            if lens_type in ['PIXELATED', 'PIXELATED_DIRAC']:
                mass_model_class = self._import_class(
                    lens_type, pixel_derivative_type=pixel_derivative_type, pixel_interpol=pixel_interpol
                )
                pix_idx = idx
            else:
                if lens_type not in imported_classes.keys():
                    mass_model_class = self._import_class(
                        lens_type, no_complex_numbers=no_complex_numbers, 
                        kwargs_pixel_grid_fixed=kwargs_pixel_grid_fixed,
                    )
                    imported_classes.update({lens_type: mass_model_class})
                else:
                    mass_model_class = imported_classes[lens_type]
            func_list.append(mass_model_class)
        return func_list, pix_idx

    @staticmethod
    def _import_class(
            lens_type, pixel_derivative_type=None, pixel_interpol=None, 
            no_complex_numbers=None, kwargs_pixel_grid_fixed=None
        ):
        """Get the lens profile class of the corresponding type."""
        if lens_type == 'GAUSSIAN':
            return gaussian_potential.Gaussian()
        elif lens_type == 'SHEAR':
            return shear.Shear()
        elif lens_type == 'SHEAR_GAMMA_PSI':
            return shear.ShearGammaPsi()
        elif lens_type == 'POINT_MASS':
            return point_mass.PointMass()
        elif lens_type == 'NIE':
            return nie.NIE()
        elif lens_type == 'SIE':
            return sie.SIE()
        elif lens_type == 'SIS':
            return sis.SIS()
        elif lens_type == 'EPL':
            return epl.EPL(no_complex_numbers=no_complex_numbers)
        elif lens_type == 'MULTIPOLE':
            return multipole.Multipole()
        elif lens_type == 'PIXELATED':
            return pixelated.PixelatedPotential(derivative_type=pixel_derivative_type, interpolation_type=pixel_interpol)
        elif lens_type == 'PIXELATED_DIRAC':
            return pixelated.PixelatedPotentialDirac()
        elif lens_type == 'PIXELATED_FIXED':
            if kwargs_pixel_grid_fixed is None:
                raise ValueError("At least one pixel grid must be provided to use 'PIXELATED_FIXED' profile")
            return pixelated.PixelatedFixed(**kwargs_pixel_grid_fixed)
        # Check if the lens is actually a class instead of a string
        elif isinstance(lens_type, type):
            return lens_type()
        else:
            err_msg = (f"{lens_type} is not a valid lens model. " +
                       f"Supported types are {SUPPORTED_MODELS}")
            raise ValueError(err_msg)

    def _bool_list(self, k):
        return util.convert_bool_list(n=self._num_func, k=k)

    @property
    def has_pixels(self):
        return self._pix_idx is not None

    @property
    def pixel_grid_settings(self):
        return self._kwargs_pixelated

    def set_pixel_grid(self, pixel_grid):
        self.func_list[self.pixelated_index].set_pixel_grid(pixel_grid)

    @property
    def pixel_grid(self):
        if not self.has_pixels:
            return None
        return self.func_list[self.pixelated_index].pixel_grid

    @property
    def pixelated_index(self):
        # TODO: support multiple pixelated profiles
        return self._pix_idx

    @property
    def pixelated_coordinates(self):
        if not self.has_pixels:
            return None, None
        return self.pixel_grid.pixel_coordinates

    @property
    def pixelated_shape(self):
        if not self.has_pixels:
            return None
        x_coords, _ = self.pixelated_coordinates
        return x_coords.shape
