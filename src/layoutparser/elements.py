from abc import ABC, abstractmethod
from collections.abc import Iterable
from copy import copy, deepcopy
from inspect import getmembers, isfunction
import warnings
import functools
import numpy as np
import pandas as pd
from PIL import Image
from cv2 import getPerspectiveTransform as _getPerspectiveTransform
from cv2 import warpPerspective as _warpPerspective

__all__ = ['Interval', 'Rectangle', 'Quadrilateral', 'TextBlock', 'Layout']


def _cvt_coordinates_to_points(coords):

    x_1, y_1, x_2, y_2 = coords
    return np.array([[x_1, y_1],  # Top Left
                     [x_2, y_1],  # Top Right
                     [x_2, y_2],  # Bottom Right
                     [x_1, y_2],  # Bottom Left
                     ])


def _cvt_points_to_coordinates(points):
    x_1 = points[:, 0].min()
    y_1 = points[:, 1].min()
    x_2 = points[:, 0].max()
    y_2 = points[:, 1].max()
    return (x_1, y_1, x_2, y_2)


def _perspective_transformation(M, points, is_inv=False):

    if is_inv:
        M = np.linalg.inv(M)

    src_mid = np.hstack([points, np.ones((points.shape[0], 1))]).T  # 3x4
    dst_mid = np.matmul(M, src_mid)

    dst = (dst_mid/dst_mid[-1]).T[:, :2]  # 4x2

    return dst


def _vertice_in_polygon(vertice, polygon_points):
    # The polygon_points are ordered clockwise

    # The implementation is based on the algorithm from
    # https://demonstrations.wolfram.com/AnEfficientTestForAPointToBeInAConvexPolygon/

    points = polygon_points - vertice  # shift the coordinates origin to the vertice
    edges = np.append(points, points[0:1, :], axis=0)
    return all([np.linalg.det([e1, e2]) >= 0 for e1, e2 in zip(edges, edges[1:])])
    # If the points are ordered clockwise, the det should <=0


def _parse_datatype_from_feature_names(feature_names):

    type_feature_map = {
        Interval: set(Interval.feature_names),
        Rectangle: set(Rectangle.feature_names),
        Quadrilateral: set(Quadrilateral.feature_names)
    }

    for cls, fnames in type_feature_map.items():
        if set(feature_names) == fnames:
            return cls

    raise ValueError(
        "\n "
        "\n The input feature is incompatible with the designated format."
        "\n Please check the tutorials for more details."
        "\n "
    )


def _polygon_area(xs, ys):
    """Calculate the area of polygons using 
    `Shoelace Formula <https://en.wikipedia.org/wiki/Shoelace_formula>`_.

    Args:
        xs (`np.ndarray`): The x coordinates of the points
        ys (`np.ndarray`): The y coordinates of the points
    """

    # Refer to: https://stackoverflow.com/questions/24467972/calculate-area-of-polygon-given-x-y-coordinates
    # The formula is equivalent to the original one indicated in the wikipedia
    # page.

    return 0.5*np.abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1)))


def mixin_textblock_meta(func):
    @functools.wraps(func)
    def wrap(self, *args, **kwargs):
        out = func(self, *args, **kwargs)
        if isinstance(out, BaseCoordElement):
            self = copy(self)
            self.block = out
            return self
    return wrap


def inherit_docstrings(cls=None, *, base_class=None):

    # Refer to https://stackoverflow.com/a/17393254
    if cls is None:
        return functools.partial(inherit_docstrings, base_class=base_class)

    for name, func in getmembers(cls, isfunction):
        if func.__doc__:
            continue
        if base_class == None:
            for parent in cls.__mro__[1:]:
                if hasattr(parent, name):
                    func.__doc__ = getattr(parent, name).__doc__
                    break
        else:
            if hasattr(base_class, name):
                func.__doc__ = getattr(base_class, name).__doc__

    return cls


def support_textblock(func):
    @functools.wraps(func)
    def wrap(self, other, *args, **kwargs):
        if isinstance(other, TextBlock):
            other = other.block
        out = func(self, other, *args, **kwargs)
        return out
    return wrap


class BaseLayoutElement():

    def set(self, inplace=False, **kwargs):

        obj = self if inplace else copy(self)
        var_dict = vars(obj)
        for key, val in kwargs.items():
            if key in var_dict:
                var_dict[key] = val
            elif f"_{key}" in var_dict:
                var_dict[f"_{key}"] = val
            else:
                raise ValueError(f"Unknown attribute name: {key}")

        return obj

    def __repr__(self):

        info_str = ', '.join(
            [f'{key}={val}' for key, val in vars(self).items()])
        return f"{self.__class__.__name__}({info_str})"

    def __eq__(self, other):

        if other.__class__ is not self.__class__:
            return False

        return vars(self) == vars(other)


class BaseCoordElement(ABC, BaseLayoutElement):

    #######################################################################
    #########################  Layout Properties  #########################
    #######################################################################

    @property
    @abstractmethod
    def width(self): pass

    @property
    @abstractmethod
    def height(self): pass

    @property
    @abstractmethod
    def coordinates(self): pass

    @property
    @abstractmethod
    def points(self): pass

    @property
    @abstractmethod
    def area(self): pass

    #######################################################################
    ### Geometric Relations (relative to, condition on, and is in)  ###
    #######################################################################

    @abstractmethod
    def condition_on(self, other):
        """
        Given the current element in relative coordinates to another element which is in absolute coordinates,
        generate a new element of the current element in absolute coordinates.

        Args:
            other (:obj:`BaseCoordElement`): 
                The other layout element involved in the geometric operations.

        Raises:
            Exception: Raise error when the input type of the other element is invalid.

        Returns:
            :obj:`BaseCoordElement`: 
                The BaseCoordElement object of the original element in the absolute coordinate system.
        """

        pass

    @abstractmethod
    def relative_to(self, other):
        """
        Given the current element and another element both in absolute coordinates,
        generate a new element of the current element in relative coordinates to the other element.

        Args:
            other (:obj:`BaseCoordElement`): The other layout element involved in the geometric operations.

        Raises:
            Exception: Raise error when the input type of the other element is invalid.

        Returns:
            :obj:`BaseCoordElement`: 
                The BaseCoordElement object of the original element in the relative coordinate system.
        """

        pass

    @abstractmethod
    def is_in(self, other, soft_margin={}, center=False):
        """
        Identify whether the current element is within another element. 

        Args:
            other (:obj:`BaseCoordElement`): 
                The other layout element involved in the geometric operations.
            soft_margin (:obj:`dict`, `optional`, defaults to `{}`): 
                Enlarge the other element with wider margins to relax the restrictions.  
            center (:obj:`bool`, `optional`, defaults to `False`): 
                The toggle to determine whether the center (instead of the four corners) 
                of the current element is in the other element.

        Returns:
            :obj:`bool`: Returns `True` if the current element is in the other element and `False` if not.
        """

        pass

    #######################################################################
    ############### Geometric Operations (pad, shift, scale) ##############
    #######################################################################

    @abstractmethod
    def pad(self, left=0, right=0, top=0, bottom=0,
            safe_mode=True):
        """ Pad the layout element on the four sides of the polygon with the user-defined pixels. If 
        safe_mode is set to True, the function will cut off the excess padding that falls on the negative 
        side of the coordinates.

        Args:
            left (:obj:`int`, `optional`, defaults to 0): The number of pixels to pad on the upper side of the polygon.
            right (:obj:`int`, `optional`, defaults to 0): The number of pixels to pad on the lower side of the polygon.
            top (:obj:`int`, `optional`, defaults to 0): The number of pixels to pad on the left side of the polygon.
            bottom (:obj:`int`, `optional`, defaults to 0): The number of pixels to pad on the right side of the polygon.
            safe_mode (:obj:`bool`, `optional`, defaults to True): A bool value to toggle the safe_mode.

        Returns:
            :obj:`BaseCoordElement`: The padded BaseCoordElement object.
        """

        pass

    @abstractmethod
    def shift(self, shift_distance=0):
        """
        Shift the layout element by user specified amounts on x and y axis respectively. If shift_distance is one
        numeric value, the element will by shifted by the same specified amount on both x and y axis.

        Args:
            shift_distance (:obj:`numeric` or :obj:`Tuple(numeric)` or :obj:`List[numeric]`): 
                The number of pixels used to shift the element.

        Returns:
            :obj:`BaseCoordElement`: The shifted BaseCoordElement of the same shape-specific class.
        """

        pass

    @abstractmethod
    def scale(self, scale_factor=1):
        """
        Scale the layout element by a user specified amount on x and y axis respectively. If scale_factor is one
        numeric value, the element will by scaled by the same specified amount on both x and y axis.

        Args:
            scale_factor (:obj:`numeric` or :obj:`Tuple(numeric)` or :obj:`List[numeric]`): The amount for downscaling or upscaling the element.

        Returns:
            :obj:`BaseCoordElement`: The scaled BaseCoordElement of the same shape-specific class.
        """

        pass
    #######################################################################
    ################################# MISC ################################
    #######################################################################

    @abstractmethod
    def crop_image(self, image):
        """
        Crop the input image according to the coordinates of the element.

        Args:
            image (:obj:`Numpy array`): The array of the input image.

        Returns:
            :obj:`Numpy array`: The array of the cropped image.
        """

        pass


@inherit_docstrings
class Interval(BaseCoordElement):
    """
    This class describes the coordinate system of an interval, a block defined by a pair of start and end point 
    on the designated axis and same length as the base canvas on the other axis.

    Args:
        start (:obj:`numeric`): 
            The coordinate of the start point on the designated axis.
        end (:obj:`numeric`): 
            The end coordinate on the same axis as start.
        axis (:obj:`str`, optional`, defaults to 'x'): 
            The designated axis that the end points belong to.
        canvas_height (:obj:`numeric`, `optional`, defaults to 0): 
            The height of the canvas that the interval is on.
        canvas_width (:obj:`numeric`, `optional`, defaults to 0): 
            The width of the canvas that the interval is on.
    """

    name = "_interval"
    feature_names = ["x_1", "y_1", "x_2", "y_2", "height", "width"]

    def __init__(self, start, end, axis='x',
                 canvas_height=0, canvas_width=0):

        assert start <= end, f"Invalid input for start and end. Start must <= end."
        self.start = start
        self.end = end

        assert axis in [
            'x', 'y'], f"Invalid axis {axis}. Axis must be in 'x' or 'y'"
        self.axis = axis

        self.canvas_height = canvas_height
        self.canvas_width = canvas_width

    @property
    def height(self):
        """
        Calculate the height of the interval. If the interval is along the x-axis, the height will be the 
        height of the canvas, otherwise, it will be the difference between the start and end point.

        Returns:
            :obj:`numeric`: Output the numeric value of the height.
        """

        if self.axis == 'x':
            return self.canvas_height
        else:
            return self.end - self.start

    @property
    def width(self):
        """
        Calculate the width of the interval. If the interval is along the y-axis, the width will be the 
        width of the canvas, otherwise, it will be the difference between the start and end point.

        Returns:
            :obj:`numeric`: Output the numeric value of the width.
        """

        if self.axis == 'y':
            return self.canvas_width
        else:
            return self.end - self.start

    @property
    def coordinates(self):
        """
        This method considers an interval as a rectangle and calculates the coordinates of the upper left 
        and lower right corners to define the interval.

        Returns:
            :obj:`Tuple(numeric)`: 
                Output the numeric values of the coordinates in a Tuple of size four. 
        """

        if self.axis == 'x':
            coords = (self.start, 0, self.end, self.canvas_height)
        else:
            coords = (0, self.start, self.canvas_width, self.end)

        return coords

    @property
    def points(self):
        """
        Return the coordinates of all four corners of the interval in a clockwise fashion 
        starting from the upper left. 

        Returns:
            :obj:`Numpy array`: A Numpy array of shape 4x2 containing the coordinates.
        """

        return _cvt_coordinates_to_points(self.coordinates)

    @property
    def center(self):
        """
        Calculate the mid-point between the start and end point.

        Returns:
            :obj:`Tuple(numeric)`: Returns of coordinate of the center.
        """

        return (self.start + self.end) / 2.

    @property
    def area(self):
        """Return the area of the covered region of the interval. 
        The area is bounded to the canvas. If the interval is put
        on a canvas, the area equals to interval width * canvas height 
        (axis='x') or interval height * canvas width (axis='y'). 
        Otherwise, the area is zero.
        """
        return self.height * self.width

    def put_on_canvas(self, canvas):
        """
        Set the height and the width of the canvas that the interval is on.

        Args:
            canvas (:obj:`Numpy array` or :obj:`BaseCoordElement` or :obj:`PIL.Image.Image`): 
                The base element that the interval is on. The numpy array should be the 
                format of `[height, width]`.

        Returns:
            :obj:`Interval`: 
                A copy of the current Interval with its canvas height and width set to 
                those of the input canvas.
        """

        if isinstance(canvas, np.ndarray):
            h, w = canvas.shape[:2]
        elif isinstance(canvas, BaseCoordElement):
            h, w = canvas.height, canvas.width
        elif isinstance(canvas, Image.Image):
            w, h = canvas.size
        else:
            raise NotImplementedError

        return self.set(canvas_height=h, canvas_width=w)

    @support_textblock
    def condition_on(self, other):

        if isinstance(other, Interval):
            if other.axis == self.axis:
                d = other.start
                # Reset the canvas size in the absolute coordinates
                return self.__class__(self.start + d, self.end + d, self.axis)
            else:
                return copy(self)

        elif isinstance(other, Rectangle):

            return (self
                    .put_on_canvas(other)
                    .to_rectangle()
                    .condition_on(other)
                    )

        elif isinstance(other, Quadrilateral):

            return (self
                    .put_on_canvas(other)
                    .to_quadrilateral()
                    .condition_on(other)
                    )

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    @support_textblock
    def relative_to(self, other):

        if isinstance(other, Interval):
            if other.axis == self.axis:
                d = other.start
                # Reset the canvas size in the absolute coordinates
                return self.__class__(self.start - d, self.end - d, self.axis)
            else:
                return copy(self)

        elif isinstance(other, Rectangle):

            return (self
                    .put_on_canvas(other)
                    .to_rectangle()
                    .relative_to(other)
                    )

        elif isinstance(other, Quadrilateral):

            return (self
                    .put_on_canvas(other)
                    .to_quadrilateral()
                    .relative_to(other)
                    )

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    @support_textblock
    def is_in(self, other, soft_margin={}, center=False):

        other = other.pad(**soft_margin)

        if isinstance(other, Interval):
            if self.axis != other.axis:
                return False
            else:
                if not center:
                    return other.start <= self.start <= self.end <= other.end
                else:
                    return other.start <= self.center <= other.end

        elif isinstance(other, Rectangle) or isinstance(other, Quadrilateral):
            x_1, y_1, x_2, y_2 = other.coordinates

            if center:
                if self.axis == 'x':
                    return x_1 <= self.center <= x_2
                else:
                    return y_1 <= self.center <= y_2
            else:
                if self.axis == 'x':
                    return x_1 <= self.start <= self.end <= x_2
                else:
                    return y_1 <= self.start <= self.end <= y_2

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    def pad(self, left=0, right=0, top=0, bottom=0, safe_mode=True):

        if self.axis == 'x':
            start = self.start - left
            end = self.end + right
            if top or bottom:
                warnings.warn(
                    f"Invalid padding top/bottom for an x axis {self.__class__.__name__}")
        else:
            start = self.start - top
            end = self.end + bottom
            if left or right:
                warnings.warn(
                    f"Invalid padding right/left for a y axis {self.__class__.__name__}")

        if safe_mode:
            start = max(0, start)

        return self.set(start=start, end=end)

    def shift(self, shift_distance):
        """
        Shift the interval by a user specified amount along the same axis that the interval is defined on.

        Args:
            shift_distance (:obj:`numeric`): The number of pixels used to shift the interval.

        Returns:
            :obj:`BaseCoordElement`: The shifted Interval object.
        """

        if isinstance(shift_distance, Iterable):
            shift_distance = shift_distance[0] if self.axis == 'x' \
                else shift_distance[1]
            warnings.warn(
                f"Input shift for multiple axes. Only use the distance for the {self.axis} axis")

        start = self.start + shift_distance
        end = self.end + shift_distance
        return self.set(start=start, end=end)

    def scale(self, scale_factor):
        """
        Scale the layout element by a user specified amount the same axis that the interval is defined on.

        Args:
            scale_factor (:obj:`numeric`): The amount for downscaling or upscaling the element.

        Returns:
            :obj:`BaseCoordElement`: The scaled Interval object.
        """

        if isinstance(scale_factor, Iterable):
            scale_factor = scale_factor[0] if self.axis == 'x' \
                else scale_factor[1]
            warnings.warn(
                f"Input scale for multiple axes. Only use the factor for the {self.axis} axis")

        start = self.start * scale_factor
        end = self.end * scale_factor
        return self.set(start=start, end=end)

    def crop_image(self, image):
        x_1, y_1, x_2, y_2 = self.put_on_canvas(image).coordinates
        return image[int(y_1):int(y_2), int(x_1):int(x_2)]

    def to_rectangle(self):
        """ 
        Convert the Interval to a Rectangle element.

        Returns:
            :obj:`Rectangle`: The converted Rectangle object.
        """
        return Rectangle(*self.coordinates)

    def to_quadrilateral(self):
        """
        Convert the Interval to a Quadrilateral element.

        Returns:
            :obj:`Quadrilateral`: The converted Quadrilateral object.
        """
        return Quadrilateral(self.points)

    @classmethod
    def from_series(cls, series):
        series = series.dropna()
        if series.get('x_1') and series.get('x_2'):
            axis = 'x'
            start, end = series.get('x_1'), series.get('x_2')
        else:
            axis = 'y'
            start, end = series.get('y_1'), series.get('y_2')

        return cls(start, end, axis=axis,
                   canvas_height=series.get('height') or 0,
                   canvas_width=series.get('width') or 0)


@inherit_docstrings
class Rectangle(BaseCoordElement):
    """
    This class describes the coordinate system of an axial rectangle box using two points as indicated below::

            (x_1, y_1) ----
            |             |
            |             |
            |             |
            ---- (x_2, y_2)

    Args:
        x_1 (:obj:`numeric`): 
            x coordinate on the horizontal axis of the upper left corner of the rectangle.
        y_1 (:obj:`numeric`): 
            y coordinate on the vertical axis of the upper left corner of the rectangle.
        x_2 (:obj:`numeric`): 
            x coordinate on the horizontal axis of the lower right corner of the rectangle.
        y_2 (:obj:`numeric`): 
            y coordinate on the vertical axis of the lower right corner of the rectangle.
    """

    name = "_rectangle"
    feature_names = ["x_1", "y_1", "x_2", "y_2"]

    def __init__(self, x_1, y_1, x_2, y_2):

        self.x_1 = x_1
        self.y_1 = y_1
        self.x_2 = x_2
        self.y_2 = y_2

    @property
    def height(self):
        """
        Calculate the height of the rectangle.

        Returns:
            :obj:`numeric`: Output the numeric value of the height.
        """

        return self.y_2 - self.y_1

    @property
    def width(self):
        """
        Calculate the width of the rectangle.

        Returns:
            :obj:`numeric`: Output the numeric value of the width.
        """

        return self.x_2 - self.x_1

    @property
    def coordinates(self):
        """
        Return the coordinates of the two points that define the rectangle.

        Returns:
            :obj:`Tuple(numeric)`: Output the numeric values of the coordinates in a Tuple of size four. 
        """

        return (self.x_1, self.y_1, self.x_2, self.y_2)

    @property
    def points(self):
        """
        Return the coordinates of all four corners of the rectangle in a clockwise fashion 
        starting from the upper left. 

        Returns:
            :obj:`Numpy array`: A Numpy array of shape 4x2 containing the coordinates.
        """

        return _cvt_coordinates_to_points(self.coordinates)

    @property
    def center(self):
        """
        Calculate the center of the rectangle.

        Returns:
            :obj:`Tuple(numeric)`: Returns of coordinate of the center.
        """

        return (self.x_1 + self.x_2)/2., (self.y_1 + self.y_2)/2.

    @property
    def area(self):
        """
        Return the area of the rectangle.
        """
        return self.width * self.height

    @support_textblock
    def condition_on(self, other):

        if isinstance(other, Interval):
            if other.axis == 'x':
                dx, dy = other.start, 0
            else:
                dx, dy = 0, other.start

            return self.__class__(self.x_1 + dx, self.y_1 + dy,
                                  self.x_2 + dx, self.y_2 + dy)

        elif isinstance(other, Rectangle):
            dx, dy, _, _ = other.coordinates

            return self.__class__(self.x_1 + dx, self.y_1 + dy,
                                  self.x_2 + dx, self.y_2 + dy)

        elif isinstance(other, Quadrilateral):
            transformed_points = _perspective_transformation(other.perspective_matrix,
                                                             self.points, is_inv=True)

            return other.__class__(transformed_points, self.height, self.width)

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    @support_textblock
    def relative_to(self, other):
        if isinstance(other, Interval):
            if other.axis == 'x':
                dx, dy = other.start, 0
            else:
                dx, dy = 0, other.start

            return self.__class__(self.x_1 - dx, self.y_1 - dy,
                                  self.x_2 - dx, self.y_2 - dy)

        elif isinstance(other, Rectangle):
            dx, dy, _, _ = other.coordinates

            return self.__class__(self.x_1 - dx, self.y_1 - dy,
                                  self.x_2 - dx, self.y_2 - dy)

        elif isinstance(other, Quadrilateral):
            transformed_points = _perspective_transformation(other.perspective_matrix,
                                                             self.points, is_inv=False)

            return other.__class__(transformed_points, self.height, self.width)

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    @support_textblock
    def is_in(self, other, soft_margin={}, center=False):

        other = other.pad(**soft_margin)

        if isinstance(other, Interval):
            if not center:
                if other.axis == 'x':
                    start, end = self.x_1, self.x_2
                else:
                    start, end = self.y_1, self.y_2
                return other.start <= start <= end <= other.end
            else:
                c = self.center[0] if other.axis == 'x' else self.center[1]
                return other.start <= c <= other.end

        elif isinstance(other, Rectangle):
            x_interval = other.to_interval(axis='x')
            y_interval = other.to_interval(axis='y')
            return self.is_in(x_interval, center=center) and \
                self.is_in(y_interval, center=center)

        elif isinstance(other, Quadrilateral):

            if not center:
                # This is equivalent to determine all the points of the
                # rectangle is in the quadrilateral.
                is_vertice_in = [_vertice_in_polygon(
                    vertice, other.points) for vertice in self.points]
                return all(is_vertice_in)
            else:
                center = np.array(self.center)
                return _vertice_in_polygon(center, other.points)

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    def pad(self, left=0, right=0, top=0, bottom=0,
            safe_mode=True):

        x_1 = self.x_1 - left
        y_1 = self.y_1 - top
        x_2 = self.x_2 + right
        y_2 = self.y_2 + bottom

        if safe_mode:
            x_1 = max(0, x_1)
            y_1 = max(0, y_1)

        return self.__class__(x_1, y_1, x_2, y_2)

    def shift(self, shift_distance=0):

        if not isinstance(shift_distance, Iterable):
            shift_x = shift_distance
            shift_y = shift_distance
        else:
            assert len(
                shift_distance) == 2, "shift_distance should have 2 elements, one for x dimension and one for y dimension"
            shift_x, shift_y = shift_distance

        x_1 = self.x_1 + shift_x
        y_1 = self.y_1 + shift_y
        x_2 = self.x_2 + shift_x
        y_2 = self.y_2 + shift_y
        return self.__class__(x_1, y_1, x_2, y_2)

    def scale(self, scale_factor=1):

        if not isinstance(scale_factor, Iterable):
            scale_x = scale_factor
            scale_y = scale_factor
        else:
            assert len(
                scale_factor) == 2, "scale_factor should have 2 elements, one for x dimension and one for y dimension"
            scale_x, scale_y = scale_factor

        x_1 = self.x_1 * scale_x
        y_1 = self.y_1 * scale_y
        x_2 = self.x_2 * scale_x
        y_2 = self.y_2 * scale_y
        return self.__class__(x_1, y_1, x_2, y_2)

    def crop_image(self, image):
        x_1, y_1, x_2, y_2 = self.coordinates
        return image[int(y_1):int(y_2), int(x_1):int(x_2)]

    def to_interval(self, axis='x', **kwargs):
        if axis == 'x':
            start, end = self.x_1, self.x_2
        else:
            start, end = self.y_1, self.y_2

        return Interval(start, end, axis=axis, **kwargs)

    def to_quadrilateral(self):
        return Quadrilateral(self.points)

    @classmethod
    def from_series(cls, series):
        series = series.dropna()
        return cls(*[series[fname] for fname in cls.feature_names])


@inherit_docstrings
class Quadrilateral(BaseCoordElement):
    """
    This class describes the coodinate system of a four-sided polygon. A quadrilateral is defined by 
    the coordinates of its 4 corners in a clockwise order starting with the upper left corner (as shown below)::

        points[0] -...- points[1]
        |                      |
        .                      .
        .                      .
        .                      .
        |                      |
        points[3] -...- points[2]

    Args:
        points (:obj:`Numpy array`):
            The array of 4 corner coordinates of size 4x2.
        height (:obj:`numeric`, `optional`, defaults to `None`):
            The height of the quadrilateral. This is to better support the perspective
            transformation from the OpenCV library.
        width (:obj:`numeric`, `optional`, defaults to `None`):
            The width of the quadrilateral. Similarly as height, this is to better support the perspective
            transformation from the OpenCV library.
    """

    name = "_quadrilateral"
    feature_names = ["p11", "p12", "p21", "p22",
                     "p31", "p32", "p41", "p42",
                     "height", "width"]

    def __init__(self, points, height=None, width=None):

        assert isinstance(
            points, np.ndarray), f" Invalid input: points must be a numpy array"

        self._points = points
        self._width = width
        self._height = height

    @property
    def height(self):
        """
        Return the user defined height, otherwise the height of its circumscribed rectangle.

        Returns:
            :obj:`numeric`: Output the numeric value of the height.
        """

        if self._height is not None:
            return self._height
        return self.points[:, 1].max() - self.points[:, 1].min()

    @property
    def width(self):
        """
        Return the user defined width, otherwise the width of its circumscribed rectangle.

        Returns:
            :obj:`numeric`: Output the numeric value of the width.
        """

        if self._width is not None:
            return self._width
        return self.points[:, 0].max() - self.points[:, 0].min()

    @property
    def coordinates(self):
        """
        Return the coordinates of the upper left and lower right corners points that 
        define the circumscribed rectangle.

        Returns
            :obj:`Tuple(numeric)`: Output the numeric values of the coordinates in a Tuple of size four. 
        """

        return _cvt_points_to_coordinates(self.points)

    @property
    def points(self):
        """
        Return the coordinates of all four corners of the quadrilateral in a clockwise fashion 
        starting from the upper left. 

        Returns:
            :obj:`Numpy array`: A Numpy array of shape 4x2 containing the coordinates.
        """

        return self._points

    @property
    def center(self):
        """
        Calculate the center of the quadrilateral.

        Returns:
            :obj:`Tuple(numeric)`: Returns of coordinate of the center.
        """

        return tuple(self.points.mean(axis=0).tolist())

    @property
    def area(self):
        """
        Return the area of the quadrilateral.
        """
        return _polygon_area(self.points[:, 0], self.points[:, 1])

    @property
    def mapped_rectangle_points(self):

        x_map = {0: 0, 1: 0, 2: self.width, 3: self.width}
        y_map = {0: 0, 1: 0, 2: self.height, 3: self.height}

        return self.map_to_points_ordering(x_map, y_map)

    @property
    def perspective_matrix(self):
        return _getPerspectiveTransform(self.points.astype('float32'),
                                        self.mapped_rectangle_points.astype('float32'))

    def map_to_points_ordering(self, x_map, y_map):

        points_ordering = self.points.argsort(axis=0).argsort(axis=0)
        # Ref: https://github.com/numpy/numpy/issues/8757#issuecomment-355126992

        return np.vstack([
            np.vectorize(x_map.get)(points_ordering[:, 0]),
            np.vectorize(y_map.get)(points_ordering[:, 1])
        ]).T

    @support_textblock
    def condition_on(self, other):

        if isinstance(other, Interval):

            if other.axis == 'x':
                return self.shift([other.start, 0])
            else:
                return self.shift([0, other.start])

        elif isinstance(other, Rectangle):

            return self.shift([other.x_1, other.y_1])

        elif isinstance(other, Quadrilateral):

            transformed_points = _perspective_transformation(other.perspective_matrix,
                                                             self.points, is_inv=True)
            return self.__class__(transformed_points, self.height, self.width)

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    @support_textblock
    def relative_to(self, other):

        if isinstance(other, Interval):

            if other.axis == 'x':
                return self.shift([-other.start, 0])
            else:
                return self.shift([0, -other.start])

        elif isinstance(other, Rectangle):

            return self.shift([-other.x_1, -other.y_1])

        elif isinstance(other, Quadrilateral):

            transformed_points = _perspective_transformation(other.perspective_matrix,
                                                             self.points, is_inv=False)
            return self.__class__(transformed_points, self.height, self.width)

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    @support_textblock
    def is_in(self, other, soft_margin={}, center=False):

        other = other.pad(**soft_margin)

        if isinstance(other, Interval):
            if not center:
                if other.axis == 'x':
                    start, end = self.coordinates[0], self.coordinates[2]
                else:
                    start, end = self.coordinates[1], self.coordinates[3]
                return other.start <= start <= end <= other.end
            else:
                c = self.center[0] if other.axis == 'x' else self.center[1]
                return other.start <= c <= other.end

        elif isinstance(other, Rectangle):
            x_interval = other.to_interval(axis='x')
            y_interval = other.to_interval(axis='y')
            return self.is_in(x_interval, center=center) and \
                self.is_in(y_interval, center=center)

        elif isinstance(other, Quadrilateral):

            if not center:
                # This is equivalent to determine all the points of the
                # rectangle is in the quadrilateral.
                is_vertice_in = [_vertice_in_polygon(
                    vertice, other.points) for vertice in self.points]
                return all(is_vertice_in)
            else:
                center = np.array(self.center)
                return _vertice_in_polygon(center, other.points)

        else:
            raise Exception(f"Invalid input type {other.__class__} for other")

    def pad(self, left=0, right=0, top=0, bottom=0,
            safe_mode=True):

        x_map = {0: -left,  1: -left,  2: right,  3: right}
        y_map = {0: -top,   1: -top,   2: bottom, 3: bottom}

        padding_mat = self.map_to_points_ordering(x_map, y_map)

        points = self.points + padding_mat
        if safe_mode:
            points = np.maximum(points, 0)

        return self.set(points=points)

    def shift(self, shift_distance=0):

        if not isinstance(shift_distance, Iterable):
            shift_mat = [shift_distance, shift_distance]
        else:
            assert len(
                shift_distance) == 2, "shift_distance should have 2 elements, one for x dimension and one for y dimension"
            shift_mat = shift_distance

        points = self.points + np.array(shift_mat)

        return self.set(points=points)

    def scale(self, scale_factor=1):

        if not isinstance(scale_factor, Iterable):
            scale_mat = [scale_factor, scale_factor]
        else:
            assert len(
                scale_factor) == 2, "scale_factor should have 2 elements, one for x dimension and one for y dimension"
            scale_mat = scale_factor

        points = self.points * np.array(scale_mat)

        return self.set(points=points)

    def crop_image(self, image):
        """
        Crop the input image using the points of the quadrilateral instance.

        Args:
            image (:obj:`Numpy array`): The array of the input image.

        Returns:
            :obj:`Numpy array`: The array of the cropped image.
        """

        return _warpPerspective(image, self.perspective_matrix, (int(self.width), int(self.height)))

    def to_interval(self, axis='x', **kwargs):

        x_1, y_1, x_2, y_2 = self.coordinates
        if axis == 'x':
            start, end = x_1, x_2
        else:
            start, end = y_1, y_2

        return Interval(start, end, axis=axis, **kwargs)

    def to_rectangle(self):
        return Rectangle(*self.coordinates)

    @classmethod
    def from_series(cls, series):
        series = series.dropna()

        points = pd.to_numeric(
            series[cls.feature_names[:8]]).values.reshape(4, -2)

        return cls(points=points,
                   height=series.get("height"),
                   width=series.get("width"))

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return False
        return np.isclose(self.points, other.points).all()

    def __repr__(self):
        keys = ['points', 'width', 'height']
        info_str = ', '.join([f'{key}={getattr(self, key)}' for key in keys])
        return f"{self.__class__.__name__}({info_str})"


@inherit_docstrings(base_class=BaseCoordElement)
class TextBlock(BaseLayoutElement):
    """
    This class constructs content-related information of a layout element in addition to its coordinate definitions 
    (i.e. Interval, Rectangle or Quadrilateral).

    Args:
        block (:obj:`BaseCoordElement`): 
            The shape-specific coordinate systems that the text block belongs to.
        text (:obj:`str`, `optional`, defaults to ""):
            The ocr'ed text results within the boundaries of the text block.
        id (:obj:`int`, `optional`, defaults to `None`):
            The id of the text block.
        type (:obj:`int`, `optional`, defaults to `None`):
            The type of the text block.
        parent (:obj:`int`, `optional`, defaults to `None`):
            The id of the parent object.
        next (:obj:`int`, `optional`, defaults to `None`):
            The id of the next block.
        score (:obj:`numeric`, defaults to `None`):
            The prediction confidence of the block
    """

    name = "_textblock"
    feature_names = ["text", "id", "type", "parent", "next", "score"]

    def __init__(self, block, text="",
                 id=None, type=None, parent=None,
                 next=None, score=None):

        assert isinstance(block, BaseCoordElement)
        self.block = block

        self.text = text
        self.id = id
        self.type = type
        self.parent = parent
        self.next = next
        self.score = score

    @property
    def height(self):
        """
        Return the height of the shape-specific block.

        Returns:
            :obj:`numeric`: Output the numeric value of the height.
        """

        return self.block.height

    @property
    def width(self):
        """
        Return the width of the shape-specific block.

        Returns:
            :obj:`numeric`: Output the numeric value of the width.
        """

        return self.block.width

    @property
    def coordinates(self):
        """
        Return the coordinates of the two corner points that define the shape-specific block.

        Returns:
            :obj:`Tuple(numeric)`: Output the numeric values of the coordinates in a Tuple of size four. 
        """

        return self.block.coordinates

    @property
    def points(self):
        """
        Return the coordinates of all four corners of the shape-specific block in a clockwise fashion 
        starting from the upper left. 

        Returns:
            :obj:`Numpy array`: A Numpy array of shape 4x2 containing the coordinates.
        """

        return self.block.points

    @property
    def area(self):
        """
        Return the area of associated block.
        """
        return self.block.area

    @mixin_textblock_meta
    def condition_on(self, other):
        return self.block.condition_on(other)

    @mixin_textblock_meta
    def relative_to(self, other):
        return self.block.relative_to(other)

    def is_in(self, other, **kwargs):
        return self.block.is_in(other, **kwargs)

    @mixin_textblock_meta
    def shift(self, shift_distance):
        return self.block.shift(shift_distance)

    @mixin_textblock_meta
    def pad(self, **kwargs):
        return self.block.pad(**kwargs)

    @mixin_textblock_meta
    def scale(self, scale_factor):
        return self.block.scale(scale_factor)

    def crop_image(self, image):
        return self.block.crop_image(image)

    @classmethod
    def from_series(cls, series):

        features = {fname: series.get(fname) for fname in cls.feature_names}
        series = series.dropna()
        if set(Quadrilateral.feature_names[:8]).issubset(series.index):
            target_type = Quadrilateral
        elif set(Interval.feature_names).issubset(series.index):
            target_type = Interval
        elif set(Rectangle.feature_names).issubset(series.index):
            target_type = Rectangle
        else:
            target_type = Interval

        return cls(
            block=target_type.from_series(series),
            **features)


class Layout(list):
    """ A handy class for handling a list of text blocks. All the class functions will be broadcasted to
    each element block in the list.
    """

    identifier_map = {
        Interval.name:      Interval,
        Rectangle.name:     Rectangle,
        Quadrilateral.name: Quadrilateral,
        TextBlock.name:     TextBlock}

    def relative_to(self, other):
        return self.__class__([ele.relative_to(other) for ele in self])

    def condition_on(self, other):
        return self.__class__([ele.condition_on(other) for ele in self])

    def is_in(self, other, **kwargs):
        return self.__class__([ele.is_in(other, **kwargs) for ele in self])

    def filter_by(self, other, **kwargs):
        """
        Return a `Layout` object containing the elements that are in the `other` object.

        Args:
            other (:obj:`BaseCoordElement`)

        Returns:
            :obj:`Layout`
        """
        return self.__class__([ele for ele in self if ele.is_in(other, **kwargs)])

    @functools.wraps(BaseCoordElement.shift)
    def shift(self, shift_distance):
        return self.__class__([ele.shift(shift_distance) for ele in self])

    @functools.wraps(BaseCoordElement.pad)
    def pad(self, **kwargs):
        return self.__class__([ele.pad(**kwargs) for ele in self])

    @functools.wraps(BaseCoordElement.scale)
    def scale(self, scale_factor):
        return self.__class__([ele.scale(scale_factor) for ele in self])

    def crop_image(self, image):
        return [ele.crop_image(image) for ele in self]

    def get_texts(self):
        """
        Iterate through all the text blocks in the list and append their ocr'ed text results.

        Returns:
            :obj:`List[str]`: A list of text strings of the text blocks in the list of layout elements.
        """

        return [ele.text for ele in self if hasattr(ele, 'text')]

    def get_info(self, attr_name):
        """Given user-provided attribute name, check all the elements in the list and return the corresponding
        attribute values.

        Args:
            attr_name (:obj:`str`): The text string of certain attribute name.

        Returns:
            :obj:`List`: 
                The list of the corresponding attribute value (if exist) of each element in the list. 
        """
        return [getattr(ele, attr_name) for ele in self if hasattr(ele, attr_name)]

    @classmethod
    def from_dataframe(cls, df):

        if "_identifier" in df.columns:
            return cls(
                [cls.identifier_map[series["_identifier"]].from_series(series.drop(columns=["_identifier"]))
                    for (_, series) in df.iterrows()]
            )

        elif any(col in TextBlock.feature_names for col in df.columns):

            return cls(
                [TextBlock.from_series(series)
                    for (_, series) in df.iterrows()]
            )

        else:
            target_type = _parse_datatype_from_feature_names(df.columns)

            return cls(
                [target_type.from_series(series)
                    for (_, series) in df.iterrows()]
            )
