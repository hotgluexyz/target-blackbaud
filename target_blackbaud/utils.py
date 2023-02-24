def clean_null(input):
    if isinstance(input, list):
        return [clean_null(i) for i in input]
    elif isinstance(input, dict):
        output = {}
        for k, v in input.items():
            if v is not None:
                output[k] = clean_null(v)
        return output
    return input