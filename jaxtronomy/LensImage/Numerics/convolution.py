from scipy import fftpack #, ndimage, signal
import numpy as np
import jax.numpy as jnp
from jax.scipy import signal
from jaxtronomy.Util.jax_util import GaussianFilter
# import threading
#from scipy._lib._version import NumpyVersion
# _rfft_mt_safe = True  # (NumpyVersion(np.__version__) >= '1.9.0.dev-e24486e')
# _rfft_lock = threading.Lock()

import jaxtronomy.Util.kernel_util as kernel_util
import jaxtronomy.Util.util as util
import jaxtronomy.Util.image_util as image_util


# def _centered(arr, newshape):
#     # Return the center newshape portion of the array.
#     newshape = np.asarray(newshape)
#     currshape = np.array(arr.shape)
#     startind = (currshape - newshape) // 2
#     endind = startind + newshape
#     myslice = [slice(startind[k], endind[k]) for k in range(len(endind))]
#     return arr[tuple(myslice)]


class PixelKernelConvolution(object):
    """
    class to compute convolutions for a given pixelized kernel (fft, grid)
    """
    def __init__(self, kernel, convolution_type='grid'):
        """

        :param kernel: 2d array, convolution kernel
        :param convolution_type: string, 'fft', 'grid', 'fft_static' mode of 2d convolution
        """
        self._kernel = kernel
        if convolution_type not in ['grid']:  # TODO: when available in JAX, add 'fft' and/or 'fft_static' options
            raise ValueError('convolution_type %s not supported!' % convolution_type)
        self._type = convolution_type

    def pixel_kernel(self, num_pix=None):
        """
        access pixelated kernel

        :param num_pix: size of returned kernel (odd number per axis). If None, return the original kernel.
        :return: pixel kernel centered
        """
        if num_pix is not None:
            return kernel_util.cut_psf(self._kernel, num_pix)
        return self._kernel

    def copy_transpose(self):
        """

        :return: copy of the class with kernel set to the transpose of original one
        """
        return PixelKernelConvolution(self._kernel.T, convolution_type=self._type)

    def convolution2d(self, image):
        """

        :param image: 2d array (image) to be convolved
        :return: fft convolution
        """
        # image_padded = jnp.pad(image, pad_width=self.radius, mode='edge')
        image_conv = signal.convolve2d(image, self._kernel, mode='same')
        return image_conv

    def re_size_convolve(self, image_low_res, image_high_res=None):
        """

        :param image_high_res: supersampled image/model to be convolved on a regular pixel grid
        :return: convolved and re-sized image
        """
        return self.convolution2d(image_low_res)


class SubgridKernelConvolution(object):
    """
    class to compute the convolution on a supersampled grid with partial convolution computed on the regular grid
    """
    def __init__(self, kernel_supersampled, supersampling_factor, supersampling_kernel_size=None, convolution_type='fft_static'):
        """

        :param kernel_supersampled: kernel in supersampled pixels
        :param supersampling_factor: supersampling factor relative to the image pixel grid
        :param supersampling_kernel_size: number of pixels (in units of the image pixels) that are convolved with the
        supersampled kernel
        """
        n_high = len(kernel_supersampled)
        self._supersampling_factor = supersampling_factor
        numPix = int(n_high / self._supersampling_factor)
        #if self._supersampling_factor % 2 == 0:
        #    self._kernel = kernel_util.averaging_even_kernel(kernel_supersampled, self._supersampling_factor)
        #else:
        #    self._kernel = util.averaging(kernel_supersampled, numGrid=n_high, numPix=numPix)
        if supersampling_kernel_size is None:
            kernel_low_res, kernel_high_res = np.zeros((3, 3)), kernel_supersampled
            self._low_res_convolution = False
        else:
            kernel_low_res, kernel_high_res = kernel_util.split_kernel(kernel_supersampled, supersampling_kernel_size,
                                                                       self._supersampling_factor)
            self._low_res_convolution = True
        self._low_res_conv = PixelKernelConvolution(kernel_low_res, convolution_type=convolution_type)
        self._high_res_conv = PixelKernelConvolution(kernel_high_res, convolution_type=convolution_type)

    def convolution2d(self, image):
        """

        :param image: 2d array (high resoluton image) to be convolved and re-sized
        :return: convolved image
        """

        image_high_res_conv = self._high_res_conv.convolution2d(image)
        image_resized_conv = image_util.re_size(image_high_res_conv, self._supersampling_factor)
        if self._low_res_convolution is True:
            image_resized = image_util.re_size(image, self._supersampling_factor)
            image_resized_conv += self._low_res_conv.convolution2d(image_resized)
        return image_resized_conv

    def re_size_convolve(self, image_low_res, image_high_res):
        """

        :param image_high_res: supersampled image/model to be convolved on a regular pixel grid
        :return: convolved and re-sized image
        """
        image_high_res_conv = self._high_res_conv.convolution2d(image_high_res)
        image_resized_conv = image_util.re_size(image_high_res_conv, self._supersampling_factor)
        if self._low_res_convolution is True:
            image_resized_conv += self._low_res_conv.convolution2d(image_low_res)
        return image_resized_conv


class MultiGaussianConvolution(object):
    """
    class to perform a convolution consisting of multiple 2d Gaussians
    This is aimed to lead to a speed-up without significant loss of accuracy due
    to the simplified convolution kernel relative to a pixelized kernel.
    """

    def __init__(self, sigma_list, fraction_list, pixel_scale,
                 supersampling_factor=1, supersampling_convolution=False,
                 truncation=2):
        """

        :param sigma_list: list of std value of Gaussian kernel
        :param fraction_list: fraction of flux to be convoled with each Gaussian kernel
        :param pixel_scale: scale of pixel width (to convert sigmas into units of pixels)
        :param truncation: float. Truncate the filter at this many standard deviations.
        Default is 4.0.
        """
        self._num_gaussians = len(sigma_list)
        self._sigmas_scaled = jnp.array(sigma_list) / pixel_scale
        if supersampling_convolution is True:
            self._sigmas_scaled *= supersampling_factor
        self._fraction_list = jnp.array(fraction_list) / sum(fraction_list)
        assert len(self._sigmas_scaled) == len(self._fraction_list)
        self._truncation = truncation
        self._pixel_scale = pixel_scale
        self._supersampling_factor = supersampling_factor
        self._supersampling_convolution = supersampling_convolution

        self._gaussian_filters = [GaussianFilter(sigma, self._truncation)
                                  for sigma in self._sigmas_scaled]

    def convolution2d(self, image):
        """
        2d convolution

        :param image: 2d numpy array, image to be convolved
        :return: convolved image, 2d numpy array
        """
        image_conv = self._gaussian_filters[0](image) * self._fraction_list[0]
        for i in range(1, self._num_gaussians):
            image_conv += self._gaussian_filters[i](image) * self._fraction_list[i]
        return image_conv

    def re_size_convolve(self, image_low_res, image_high_res):
        """

        :param image_high_res: supersampled image/model to be convolved on a regular pixel grid
        :return: convolved and re-sized image
        """
        if self._supersampling_convolution:
            image_high_res_conv = self.convolution2d(image_high_res)
            image_resized_conv = image_util.re_size(image_high_res_conv, self._supersampling_factor)
        else:
            image_resized_conv = self.convolution2d(image_low_res)
        return image_resized_conv

    def pixel_kernel(self, num_pix):
        """
        computes a pixelized kernel from the MGE parameters

        :param num_pix: int, size of kernel (odd number per axis)
        :return: pixel kernel centered
        """
        # TODO avoid this hidden import
        from jaxtronomy.LightModel.Profiles.gaussian import MultiGaussian
        mg = MultiGaussian()
        x, y = util.make_grid(numPix=num_pix, deltapix=self._pixel_scale)
        kernel = mg.function(x, y, amp=self._fraction_list, sigma=self._sigmas_scaled)
        kernel = util.array2image(kernel)
        return kernel / jnp.sum(kernel)
