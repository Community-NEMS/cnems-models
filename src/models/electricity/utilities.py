"""A collection of functions that are used in support of the electricity model."""


# TODO:  QA uses of this.  RN, it is only used as a test helper
def annual_count(hour, m) -> int:
    """Return the aggregate weight of this hour in the representative year.

    We know the hour weight, and the hours are unique to days, so we can get the day weight.

    Parameters
    ----------
    hour : int
        the rep_hour

    Returns
    -------
    int
        the aggregate weight (count) of this hour in the rep_year.  NOT the hour weight!
    """
    weight_day = m.weight_day[m.map_hour_day[hour]]
    weight_hour = m.weight_hour[hour]
    return weight_day * weight_hour
