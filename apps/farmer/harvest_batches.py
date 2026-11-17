"""Keep harvest batch numbers stable across partial sales."""
import math


def normalize_batch_numbers(batches):
    """Return numbered copies; recognise older partial-sale fragments conservatively.

    Older sales retained the value of the original whole batch on the sold
    fragment and inserted its remainder immediately after it. Only join those
    fragments when their quantities and prices prove the relationship.
    """
    result = [dict(batch) for batch in (batches or [])]
    used_numbers = set()
    index = 0
    while index < len(result):
        batch = result[index]
        number = batch.get('batch_no') or batch.get('batch_num')
        if number is None or number == '':
            number = 1
            while str(number) in used_numbers:
                number += 1
        used_numbers.add(str(number))
        batch['batch_no'] = number

        last = index
        try:
            price = float(batch.get('price') or 0)
            original_value = float(batch.get('saleValue') or 0)
            quantity = float(batch.get('yield') or 0)
            matching_units = (batch.get('priceUnit') or batch.get('unit')) == batch.get('unit')
            if batch.get('status') == 'Sold' and price > 0 and matching_units and original_value > quantity * price + 0.5:
                for candidate_index in range(index + 1, len(result)):
                    candidate = result[candidate_index]
                    if not (candidate.get('batch_no') or candidate.get('batch_num')):
                        break
                    if any(candidate.get(key) != batch.get(key) for key in ('quality', 'flush_no', 'unit', 'priceUnit', 'price')):
                        break
                    quantity += float(candidate.get('yield') or 0)
                    if math.isclose(quantity * price, original_value, rel_tol=1e-9, abs_tol=0.51):
                        last = candidate_index
                        break
                    if quantity * price > original_value + 0.51:
                        break
        except (TypeError, ValueError, OverflowError):
            pass

        for fragment in result[index + 1:last + 1]:
            fragment['batch_no'] = number
        index = last + 1
    return result
