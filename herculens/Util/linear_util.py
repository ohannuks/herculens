import numpy as np
from scipy import sparse, linalg
import findiff

from herculens.Util.jax_util import BicubicInterpolator as Interpolator
from herculens.Util import util


def build_convolution_matrix(kernel_2d, image_shape):
    """
    Credits: https://github.com/alisaaalehi/convolution_as_multiplication

    Performs 2D convolution between input I and filter F by converting the F to a toeplitz matrix and multiply it
      with vectorizes version of I
      By : AliSaaalehi@gmail.com
      
    Arg:
    I -- 2D numpy matrix
    F -- numpy 2D matrix
    print_ir -- if True, all intermediate resutls will be printed after each step of the algorithms
    
    Returns: 
    output -- 2D numpy matrix, result of convolving I with F
    """
    def matrix_to_vector(input):
        """
        Converts the input matrix to a vector by stacking the rows in a specific way explained here

        Arg:
        input -- a numpy matrix

        Returns:
        ouput_vector -- a column vector with size input.shape[0]*input.shape[1]
        """
        input_h, input_w = input.shape
        output_vector = np.zeros(input_h*input_w, dtype=input.dtype)
        # flip the input matrix up-down because last row should go first
        input = np.flipud(input) 
        for i,row in enumerate(input):
            st = i*input_w
            nd = st + input_w
            output_vector[st:nd] = row   
        return output_vector

    def vector_to_matrix(input, output_shape):
        """
        Reshapes the output of the maxtrix multiplication to the shape "output_shape"

        Arg:
        input -- a numpy vector

        Returns:
        output -- numpy matrix with shape "output_shape"
        """
        output_h, output_w = output_shape
        output = np.zeros(output_shape, dtype=input.dtype)
        for i in range(output_h):
            st = i*output_w
            nd = st + output_w
            output[i, :] = input[st:nd]
        # flip the output matrix up-down to get correct result
        output=np.flipud(output)
        return output
    
    # number of columns and rows of the input 
    I_row_num, I_col_num = image_shape

    # number of columns and rows of the filter
    F = np.copy(kernel_2d)
    F /= F.sum()  # makes sure the kernel is normalized
    F_row_num, F_col_num = F.shape

    #  calculate the output dimensions
    output_row_num = I_row_num + F_row_num - 1
    output_col_num = I_col_num + F_col_num - 1
    # if print_ir: print('output dimension:', output_row_num, output_col_num)

    # zero pad the filter
    F_zero_padded = np.pad(F, ((output_row_num - F_row_num, 0),
                               (0, output_col_num - F_col_num)),
                            'constant', constant_values=0)
    # if print_ir: print('F_zero_padded: ', F_zero_padded)

    # use each row of the zero-padded F to creat a toeplitz matrix. 
    #  Number of columns in this matrices are same as numbe of columns of input signal
    toeplitz_list = []
    for i in range(F_zero_padded.shape[0]-1, -1, -1): # iterate from last row to the first row
        c = F_zero_padded[i, :] # i th row of the F 
        r = np.r_[c[0], np.zeros(I_col_num-1)] # first row for the toeplitz fuction should be defined otherwise
                                                            # the result is wrong
        toeplitz_m = linalg.toeplitz(c, r) # this function is in scipy.linalg library
        toeplitz_list.append(toeplitz_m)
        # if print_ir: print('F '+ str(i)+'\n', toeplitz_m)

        # doubly blocked toeplitz indices: 
    #  this matrix defines which toeplitz matrix from toeplitz_list goes to which part of the doubly blocked
    c = range(1, F_zero_padded.shape[0]+1)
    r = np.r_[c[0], np.zeros(I_row_num-1, dtype=int)]
    doubly_indices = linalg.toeplitz(c, r)
    # if print_ir: print('doubly indices \n', doubly_indices)

    ## creat doubly blocked matrix with zero values
    toeplitz_shape = toeplitz_list[0].shape # shape of one toeplitz matrix
    h = toeplitz_shape[0]*doubly_indices.shape[0]
    w = toeplitz_shape[1]*doubly_indices.shape[1]
    doubly_blocked_shape = [h, w]
    doubly_blocked = np.zeros(doubly_blocked_shape)

    # tile toeplitz matrices for each row in the doubly blocked matrix
    b_h, b_w = toeplitz_shape # hight and withs of each block
    for i in range(doubly_indices.shape[0]):
        for j in range(doubly_indices.shape[1]):
            start_i = i * b_h
            start_j = j * b_w
            end_i = start_i + b_h
            end_j = start_j + b_w
            doubly_blocked[start_i: end_i, start_j:end_j] = toeplitz_list[doubly_indices[i,j]-1]

    # if print_ir: print('doubly_blocked: ', doubly_blocked)

    # convert I to a vector
    #vectorized_I = matrix_to_vector(np.copy(I))
    # if print_ir: print('vectorized_I: ', vectorized_I)
    
    # get result of the convolution by matrix mupltiplication
    #result_vector = np.matmul(doubly_blocked, vectorized_I)
    #if print_ir: print('result_vector: ', result_vector)

    # reshape the raw rsult to desired matrix form
    out_shape = (output_row_num, output_col_num)
    #output_full = vector_to_matrix(result_vector, out_shape)
    #if print_ir: print('Result of implemented method: \n', output)
        
    n_row_crop = (output_row_num - I_row_num) // 2
    n_col_crop = (output_col_num - I_col_num) // 2
    #output = output_full[n_row_crop:-n_row_crop, n_col_crop:-n_col_crop]
    
    return doubly_blocked, out_shape, n_row_crop, n_col_crop


def build_bilinear_interpol_matrix(x_grid_1d_in, y_grid_1d_in, x_grid_1d_out, 
                                   y_grid_1d_out, warning=True):
    """
    Only works with square input and output grids.
    Author: austinpeel, originally for the package `slitronomy`.
    """
    # Standardize inputs for vectorization
    x_grid_1d_out = np.atleast_1d(x_grid_1d_out)
    y_grid_1d_out = np.atleast_1d(y_grid_1d_out)
    assert len(x_grid_1d_out) == len(y_grid_1d_out), "Input arrays must be the same size."
    num_pix_out = len(x_grid_1d_out)
    
    # Compute bin edges so that (x_coord, y_coord) lie at the grid centers
    num_pix = int(np.sqrt(x_grid_1d_in.size))
    delta_pix = np.abs(x_grid_1d_in[0] - x_grid_1d_in[1])
    half_pix = delta_pix / 2.

    x_coord = x_grid_1d_in[:num_pix]
    x_dir = -1 if x_coord[0] > x_coord[-1] else 1  # Handle x-axis inversion
    x_lower = x_coord[0] - x_dir * half_pix
    x_upper = x_coord[-1] + x_dir * half_pix
    xbins = np.linspace(x_lower, x_upper, num_pix + 1)

    y_coord = y_grid_1d_in[::num_pix]
    y_dir = -1 if y_coord[0] > y_coord[-1] else 1  # Handle y-axis inversion
    y_lower = y_coord[0] - y_dir * half_pix
    y_upper = y_coord[-1] + y_dir * half_pix
    ybins = np.linspace(y_lower, y_upper, num_pix + 1)

    # Keep only coordinates that fall within the output grid
    x_min, x_max = [x_lower, x_upper][::x_dir]
    y_min, y_max = [y_lower, y_upper][::y_dir]
    selection = ((x_grid_1d_out > x_min) & (x_grid_1d_out < x_max) &
                 (y_grid_1d_out > y_min) & (y_grid_1d_out < y_max))
    if np.any(1 - selection.astype(int)):
        x_grid_1d_out = x_grid_1d_out[selection]
        y_grid_1d_out = y_grid_1d_out[selection]
        num_pix_out = len(x_grid_1d_out)

    # Find the (1D) output pixel that (x_grid_1d_out, y_grid_1d_out) falls in
    index_x = np.digitize(x_grid_1d_out, xbins) - 1
    index_y = np.digitize(y_grid_1d_out, ybins) - 1
    index_1 = index_x + index_y * num_pix

    # Compute distances between input and output grid points
    dx = x_grid_1d_out - x_grid_1d_in[index_1]
    dy = y_grid_1d_out - y_grid_1d_in[index_1]

    # Find the three other nearest pixels (may end up out of bounds)
    index_2 = index_1 + x_dir * np.sign(dx).astype(int)
    index_3 = index_1 + y_dir * np.sign(dy).astype(int) * num_pix
    index_4 = index_2 + y_dir * np.sign(dy).astype(int) * num_pix

    # Treat these index arrays as four sets stacked vertically
    # Prepare to mask out out-of-bounds pixels as well as repeats
    # The former is important for the csr_matrix to be generated correctly
    max_index = x_grid_1d_in.size - 1  # Upper index bound
    mask = np.ones((4, num_pix_out), dtype=bool)  # Mask for the coordinates

    # Mask out any neighboring pixels that end up out of bounds
    mask[1, np.where((index_2 < 0) | (index_2 > max_index))[0]] = False
    mask[2, np.where((index_3 < 0) | (index_3 > max_index))[0]] = False
    mask[3, np.where((index_4 < 0) | (index_4 > max_index))[0]] = False

    # Mask any repeated pixels (2 or 3x) arising from unlucky grid alignment
    # zero_dx = list(np.where(dx == 0)[0])
    # zero_dy = list(np.where(dy == 0)[0])
    # unique, counts = np.unique(zero_dx + zero_dy, return_counts=True)
    # repeat_row = [ii + 1 for c in counts for ii in range(0, 3, 3 - c)]
    # repeat_col = [u for (u, c) in zip(unique, counts) for _ in range(c + 1)]
    # mask[(repeat_row, repeat_col)] = False  # TODO: this leads to strange lines

    # Generate 2D indices of non-zero elements for the sparse matrix
    row = np.tile(np.nonzero(selection)[0], (4, 1))
    col = np.array([index_1, index_2, index_3, index_4])

    # Compute bilinear weights like in Treu & Koopmans (2004)
    col[~mask] = 0  # Avoid accessing values out of bounds
    dist_x = (np.tile(x_grid_1d_out, (4, 1)) - x_grid_1d_in[col]) / delta_pix
    dist_y = (np.tile(y_grid_1d_out, (4, 1)) - y_grid_1d_in[col]) / delta_pix
    weight = (1 - np.abs(dist_x)) * (1 - np.abs(dist_y))

    # Make sure the weights are properly normalized
    # This step is only necessary where the mask has excluded source pixels
    norm = np.expand_dims(np.sum(weight, axis=0, where=mask), 0)
    weight = weight / norm

    if warning:
        if np.any(weight[mask] < 0):
            num_neg = np.sum((weight[mask] < 0).astype(int))
            print("Warning : {} weights are negative.".format(num_neg))

    indices, weights = (row[mask], col[mask]), weight[mask]

    dense_shape = (x_grid_1d_out.size, x_grid_1d_in.size)
    interpol_matrix = sparse.csr_matrix((weights, indices), shape=dense_shape)
    interpol_norm = np.squeeze(np.maximum(1, interpol_matrix.sum(axis=0)).A)
    return interpol_matrix, interpol_norm


def build_DsD_matrix(smooth_lens_image, smooth_kwargs_params, hybrid_lens_image=None):
    """this functions build the full operator from Koopmans 2005"""
    # data grid
    # x_coords, y_coords = smooth_lens_image.Grid.pixel_axes
    x_grid, y_grid = smooth_lens_image.Grid.pixel_coordinates
    num_pix_x, num_pix_y = smooth_lens_image.Grid.num_pixel_axes
    pixel_width = smooth_lens_image.Grid.pixel_width

    # pixelated lens model grid (TODO: implement interpolation on potential grid)
    # x_coords_pot, y_coords_pot = hybrid_lens_image.Grid.model_pixel_axes('lens')
    # x_grid_pot, y_grid_pot = hybrid_lens_image.Grid.model_pixel_coordinates('lens')

    # numerics grid, for intermediate computation on a higher resolution grid
    x_grid_num, y_grid_num = smooth_lens_image.ImageNumerics.coordinates_evaluate
    x_grid_num = util.array2image(x_grid_num)
    y_grid_num = util.array2image(y_grid_num)
    x_coords_num, y_coords_num = x_grid_num[0, :], y_grid_num[:, 0]
    # pixel_width_num = np.abs(x_coords_num[1] - x_coords_num[0])
    
    # get the pixelated source in source plane,
    # on the highest resolution grid possible (it will use )
    smooth_source = smooth_lens_image.SourceModel.surface_brightness(
        x_grid_num, y_grid_num, smooth_kwargs_params['kwargs_source'])
    interp_source = Interpolator(y_coords_num, x_coords_num, smooth_source)

    # compute its derivatives *on source plane*
    grad_s_x_srcplane = interp_source(y_grid_num, x_grid_num, dy=1)
    grad_s_y_srcplane = interp_source(y_grid_num, x_grid_num, dx=1)
    # grad_s_srcplane = np.sqrt(grad_s_x_srcplane**2 + grad_s_y_srcplane**2)

    # setup the Interpolator to read on data pixels
    interp_grad_s_x = Interpolator(y_coords_num, x_coords_num, grad_s_x_srcplane)
    interp_grad_s_y = Interpolator(y_coords_num, x_coords_num, grad_s_y_srcplane)

    # use the lens equation to ray shoot the coordinates of the data grid
    x_src, y_src = smooth_lens_image.LensModel.ray_shooting(
        x_grid, y_grid, smooth_kwargs_params['kwargs_lens'])

    # evaluate the resulting arrays on that grid
    grad_s_x = interp_grad_s_x(y_src, x_src)
    grad_s_y = interp_grad_s_y(y_src, x_src)
    # grad_s = np.sqrt(grad_s_x**2 + grad_s_y**2)

    # proper flux units
    grad_s_x *= pixel_width**2
    grad_s_y *= pixel_width**2

    # put them into sparse diagonal matrices
    D_s_x = sparse.diags([grad_s_x.flatten()], [0])
    D_s_y = sparse.diags([grad_s_y.flatten()], [0])

    # compute the potential derivative operator as two matrices D_x, D_y
    step_size = pixel_width # step size
    order = 1 # first-order derivative
    accuracy = 2 # accuracy of the finite difference scheme (2-points, 4-points, etc.)
    d_dx_class = findiff.FinDiff(1, step_size, order, acc=accuracy)
    d_dy_class = findiff.FinDiff(0, step_size, order, acc=accuracy)
    D_x = d_dx_class.matrix((num_pix_x, num_pix_y))
    D_y = d_dy_class.matrix((num_pix_x, num_pix_y))  # sparse matrices
    
    # join the source and potential derivatives operators
    # through minus their 'scalar' product (Eq. A6 from Koopmans 2005)
    DsD = - D_s_x.dot(D_x) - D_s_y.dot(D_y)

    # we also return the gradient of the source after being ray-traced to the data grid
    return DsD, grad_s_x, grad_s_y

