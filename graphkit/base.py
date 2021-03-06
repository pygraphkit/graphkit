# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
import logging
from collections import namedtuple

try:
    from collections import abc
except ImportError:
    import collections as abc

from . import plot


log = logging.getLogger(__name__)


## def jetsam(ex, locs, *salvage_vars, annotation="graphkit_jetsam", **salvage_mappings):  # bad PY2 syntax
def jetsam(ex, locs, *salvage_vars, **salvage_mappings):
    """
    Annotate exception with salvaged values from locals().

    :param ex:
        the exception to annotate
    :param locs:
        ``locals()`` from the context-manager's block containing vars
        to be salvaged in case of exception

        ATTENTION: wrapped function must finally call ``locals()``, because
        *locals* dictionary only reflects local-var changes after call.
    :param str annotation:
        (a kwarg not seen in the signature due to PY2 compatibility)
        the name of the attribute to attach on the exception
    :param salvage_vars:
        local variable names to save as is in the salvaged annotations dictionary.
    :param salvage_mappings:
        a mapping of destination-annotation-keys --> source-locals-keys;
        if a `source` is callable, the value to salvage is retrieved
        by calling ``value(locs)``.
        They take precendance over`salvae_vars`.

    :raise:
        any exception raised by the wrapped function, annotated with values
        assigned as atrributes on this context-manager

    - Any attrributes attached on this manager are attached as a new dict on
      the raised exception as new  ``graphkit_jetsam`` attrribute with a dict as value.
    - If the exception is already annotated, any new items are inserted,
      but existing ones are preserved.

    **Example:**

    Call it with managed-block's ``locals()`` and tell which of them to salvage
    in case of errors::


        try:
            a = 1
            b = 2
            raise Exception()
        exception Exception as ex:
            jetsam(ex, locals(), "a", b="salvaged_b", c_var="c")

    And then from a REPL::

        import sys
        sys.last_value.graphkit_jetsam
        {'a': 1, 'salvaged_b': 2, "c_var": None}

    ** Reason:**

    Graphs may become arbitrary deep.  Debugging such graphs is notoriously hard.

    The purpose is not to require a debugger-session to inspect the root-causes
    (without precluding one).

    Naively salvaging values with a simple try/except block around each function,
    blocks the debugger from landing on the real cause of the error - it would
    land on that block;  and that could be many nested levels above it.
    """
    ## Fail EARLY before yielding on bad use.
    #
    annotation = salvage_mappings.pop("annotation", "graphkit_jetsam")

    assert isinstance(ex, Exception), ("Bad `ex`, not an exception dict:", ex)
    assert isinstance(locs, dict), ("Bad `locs`, not a dict:", locs)
    assert all(isinstance(i, str) for i in salvage_vars), (
        "Bad `salvage_vars`!",
        salvage_vars,
    )
    assert salvage_mappings, "No `salvage_mappings` given!"
    assert all(isinstance(v, str) or callable(v) for v in salvage_mappings.values()), (
        "Bad `salvage_mappings`:",
        salvage_mappings,
    )

    ## Merge vars-mapping to save.
    for var in salvage_vars:
        if var not in salvage_mappings:
            salvage_mappings[var] = var

    try:
        annotations = getattr(ex, annotation, None)
        if not isinstance(annotations, dict):
            annotations = {}
            setattr(ex, annotation, annotations)

        ## Salvage those asked
        for dst_key, src in salvage_mappings.items():
            try:
                salvaged_value = src(locs) if callable(src) else locs.get(src)
                annotations.setdefault(dst_key, salvaged_value)
            except Exception as ex:
                log.warning(
                    "Supressed error while salvaging jetsam item (%r, %r): %r"
                    % (dst_key, src, ex)
                )
    except Exception as ex2:
        log.warning("Supressed error while annotating exception: %r", ex2, exc_info=1)
        raise ex2

    raise  # noqa #re-raise without ex-arg, not to insert my frame


class Data(object):
    """
    This wraps any data that is consumed or produced
    by a Operation. This data should also know how to serialize
    itself appropriately.

    This class an "abstract" class that should be extended by
    any class working with data in the HiC framework.
    """

    def __init__(self, **kwargs):
        pass

    def get_data(self):
        raise NotImplementedError

    def set_data(self, data):
        raise NotImplementedError


class Operation(object):
    """
    This is an abstract class representing a data transformation. To use this,
    please inherit from this class and customize the ``.compute`` method to your
    specific application.
    """

    def __init__(self, **kwargs):
        """
        Create a new layer instance.
        Names may be given to this layer and its inputs and outputs. This is
        important when connecting layers and data in a Network object, as the
        names are used to construct the graph.

        :param str name:
            The name the operation (e.g. conv1, conv2, etc..)

        :param list needs:
            Names of input data objects this layer requires.

        :param list provides:
            Names of output data objects this provides.

        :param dict params:
            A dict of key/value pairs representing parameters
            associated with your operation. These values will be
            accessible using the ``.params`` attribute of your object.
            NOTE: It's important that any values stored in this
            argument must be pickelable.
        """

        # (Optional) names for this layer, and the data it needs and provides
        self.name = kwargs.get("name")
        self.needs = kwargs.get("needs")
        self.provides = kwargs.get("provides")
        self.params = kwargs.get("params", {})

        # call _after_init as final step of initialization
        self._after_init()

    def __eq__(self, other):
        """
        Operation equality is based on name of layer.
        (__eq__ and __hash__ must be overridden together)
        """
        return bool(self.name is not None and self.name == getattr(other, "name", None))

    def __hash__(self):
        """
        Operation equality is based on name of layer.
        (__eq__ and __hash__ must be overridden together)
        """
        return hash(self.name)

    def compute(self, inputs):
        """
        This method must be implemented to perform this layer's feed-forward
        computation on a given set of inputs.

        :param list inputs:
            A list of :class:`Data` objects on which to run the layer's
            feed-forward computation.
        :returns list:
            Should return a list of :class:`Data` objects representing
            the results of running the feed-forward computation on
            ``inputs``.
        """

        raise NotImplementedError("Define callable of %r!" % self)

    def _compute(self, named_inputs, outputs=None):
        try:
            provides = self.provides
            args = [named_inputs[d] for d in self.needs]
            results = self.compute(args)

            results = zip(provides, results)

            if outputs:
                outs = set(outputs)
                results = filter(lambda x: x[0] in outs, results)

            return dict(results)
        except Exception as ex:
            jetsam(
                ex, locals(), "outputs", "provides", "args", "results", operation="self"
            )

    def _after_init(self):
        """
        This method is a hook for you to override. It gets called after this
        object has been initialized with its ``needs``, ``provides``, ``name``,
        and ``params`` attributes. People often override this method to implement
        custom loading logic required for objects that do not pickle easily, and
        for initialization of c++ dependencies.
        """
        pass

    def __getstate__(self):
        """
        This allows your operation to be pickled.
        Everything needed to instantiate your operation should be defined by the
        following attributes: params, needs, provides, and name
        No other piece of state should leak outside of these 4 variables
        """

        result = {}
        # this check should get deprecated soon. its for downward compatibility
        # with earlier pickled operation objects
        if hasattr(self, "params"):
            result["params"] = self.__dict__["params"]
        result["needs"] = self.__dict__["needs"]
        result["provides"] = self.__dict__["provides"]
        result["name"] = self.__dict__["name"]

        return result

    def __setstate__(self, state):
        """
        load from pickle and instantiate the detector
        """
        for k in iter(state):
            self.__setattr__(k, state[k])
        self._after_init()

    def __repr__(self):
        """
        Display more informative names for the Operation class
        """

        def aslist(i):
            if i and not isinstance(i, str):
                return list(i)
            return i

        return u"%s(name='%s', needs=%s, provides=%s)" % (
            self.__class__.__name__,
            getattr(self, "name", None),
            aslist(getattr(self, "needs", None)),
            aslist(getattr(self, "provides", None)),
        )


class NetworkOperation(Operation, plot.Plotter):
    def __init__(self, **kwargs):
        self.net = kwargs.pop("net")
        Operation.__init__(self, **kwargs)

        # set execution mode to single-threaded sequential by default
        self._execution_method = "sequential"
        self._overwrites_collector = None

    def _build_pydot(self, **kws):
        """delegate to network"""
        kws.setdefault("title", self.name)
        plotter = self.net.last_plan or self.net
        return plotter._build_pydot(**kws)

    def _compute(self, named_inputs, outputs=None):
        return self.net.compute(
            named_inputs,
            outputs,
            method=self._execution_method,
            overwrites_collector=self._overwrites_collector,
        )

    def __call__(self, *args, **kwargs):
        return self._compute(*args, **kwargs)

    def compile(self, *args, **kwargs):
        return self.net.compile(*args, **kwargs)

    def set_execution_method(self, method):
        """
        Determine how the network will be executed.

        :param str method:
            If "parallel", execute graph operations concurrently
            using a threadpool.
        """
        choices = ["parallel", "sequential"]
        if method not in choices:
            raise ValueError(
                "Invalid computation method %r!  Must be one of %s" % (method, choices)
            )
        self._execution_method = method

    def set_overwrites_collector(self, collector):
        """
        Asks to put all *overwrites* into the `collector` after computing

        An "overwrites" is intermediate value calculated but NOT stored
        into the results, becaues it has been given also as an intemediate
        input value, and the operation that would overwrite it MUST run for
        its other results.

        :param collector:
            a mutable dict to be fillwed with named values
        """
        if collector is not None and not isinstance(collector, abc.MutableMapping):
            raise ValueError(
                "Overwrites collector was not a MutableMapping, but: %r" % collector
            )
        self._overwrites_collector = collector

    def __getstate__(self):
        state = Operation.__getstate__(self)
        state["net"] = self.__dict__["net"]
        return state
