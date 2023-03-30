# Set of utilities that extends some of numpyro functionalities
# In the future, these may be incorporated within numpyro 


import jax.numpy as jnp
from functools import partial

import numpyro
from numpyro import handlers
from numpyro.distributions import transforms, constraints
from numpyro.distributions.util import sum_rightmost
from numpyro.infer import util


def unconstrain_reparam(params, site):
    """added support for numpyro.param sites"""
    name = site["name"]
    if name in params:
        p = params[name]
        
        if site["type"] == "param":
            constraint = site["kwargs"].get("constraint", constraints.real)
            with util.helpful_support_errors(site):
                transform = transforms.biject_to(constraint)
            
            len_event_shape = len(site["kwargs"]["event_dim"])

        else:
            support = site["fn"].support
            with util.helpful_support_errors(site):
                transform = transforms.biject_to(support)
            
            # in scan, we might only want to substitute an item at index i, rather than the whole sequence
            i = site["infer"].get("_scan_current_index", None)
            if i is not None:
                event_dim_shift = transform.codomain.event_dim - t.domain.event_dim
                expected_unconstrained_dim = len(site["fn"].shape()) - event_dim_shift
                # check if p has additional time dimension
                if jnp.ndim(p) > expected_unconstrained_dim:
                    p = p[i]

            if support is constraints.real or (
                isinstance(support, constraints.independent)
                and support.base_constraint is constraints.real
            ):
                return p

            len_event_shape = len(site["fn"].event_shape)

        value = transform(p)

        # NB: we add the determinant term only for sampled sites
        # and only transformed parameter site values above 
        if site["type"] == "sample":
            log_det = transform.log_abs_det_jacobian(p, value)
            log_det = sum_rightmost(
                log_det, jnp.ndim(log_det) - jnp.ndim(value) + len_event_shape
            )
            numpyro.factor("_{}_log_det".format(name), log_det)

        return value

def potential_energy(model, model_args, model_kwargs, params):
    """
    (EXPERIMENTAL INTERFACE) Computes potential energy of a model given unconstrained params.
    Under the hood, we will transform these unconstrained parameters to the values
    belong to the supports of the corresponding priors in `model`.

    :param model: a callable containing NumPyro primitives.
    :param tuple model_args: args provided to the model.
    :param dict model_kwargs: kwargs provided to the model.
    :param dict params: unconstrained parameters of `model`.
    :return: potential energy given unconstrained parameters.
    """
    substituted_model = handlers.substitute(
        model, substitute_fn=partial(unconstrain_reparam, params)
    )
    # no param is needed for log_density computation because we already substitute
    log_joint, model_trace = util.log_density(
        substituted_model, model_args, model_kwargs, {}
    )
    return -log_joint

def _transform_fn(model, model_args, model_kwargs, params, invert):
    """
    Transforms parameter values between constrained <-> unconstrained spaces.
    It supports numpyro.param sites
    """
    substituted_model = handlers.substitute(model, params)
    model_trace = handlers.trace(substituted_model).get_trace(*model_args, **model_kwargs)
    values, inv_transforms = {}, {}
    for k, v in model_trace.items():
        if v["type"] == "param":
            values[k] = v["value"]
            constraint = v["kwargs"].pop("constraint", constraints.real)
            with util.helpful_support_errors(v):
                inv_transforms[k] = transforms.biject_to(constraint)
        elif (
            v["type"] == "sample"
            and not v["is_observed"]
            and not v["fn"].support.is_discrete
        ):
            values[k] = v["value"]
            with util.helpful_support_errors(v):
                inv_transforms[k] = transforms.biject_to(v["fn"].support)
    params_const = util.transform_fn(
        inv_transforms,
        {k: v for k, v in values.items()},
        invert=invert,
    )
    return params_const

def unconstrain_fn(model, model_args, model_kwargs, params):
    return _transform_fn(model, model_args, model_kwargs, params, True)

def constrain_fn(model, model_args, model_kwargs, params):
    return _transform_fn(model, model_args, model_kwargs, params, False)

