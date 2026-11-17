(function () {
  'use strict';
  window.installFarmerTableFilters = function (table, kind) {
    var $ = window.jQuery;
    var api = $(table).DataTable();
    var bar = document.createElement('div');
    bar.className = 'farmer-list-toolbar p-3 border-bottom';
    var searchGroup = document.createElement('div');
    searchGroup.className = 'farmer-click-search';
    searchGroup.innerHTML = '<button type="button" class="btn btn-outline-secondary" data-no-loader="true" aria-label="Open search" aria-expanded="false"><i class="ri-search-line"></i></button><div class="input-group" hidden><input class="form-control" placeholder="Search records..." aria-label="Search records"><button type="button" class="btn btn-outline-secondary" data-no-loader="true" aria-label="Clear and close search"><i class="ri-close-line"></i></button></div>';
    bar.appendChild(searchGroup);
    var search = searchGroup.querySelector('input');
    var toggle = searchGroup.firstElementChild;
    toggle.onclick = function () { search.parentElement.hidden = false; toggle.hidden = true; toggle.setAttribute('aria-expanded', 'true'); search.focus(); };
    search.addEventListener('input', function () { api.search(search.value).draw(); });
    searchGroup.querySelector('.input-group button').onclick = function () { search.value = ''; api.search('').draw(); search.parentElement.hidden = true; toggle.hidden = false; toggle.setAttribute('aria-expanded', 'false'); toggle.focus(); };
    var controls = document.createElement('div');
    controls.className = 'farmer-list-filters';
    var state = document.createElement('select');
    state.className = 'form-select';
    state.setAttribute('aria-label', kind === 'customers' ? 'Outstanding balance' : 'Payment status');
    var options = kind === 'customers' ? [['', 'All balances'], ['due', 'Outstanding'], ['clear', 'No outstanding'], ['credit', 'Credit balance']] : [['', 'All statuses'], ['Paid', 'Paid'], ['Partial', 'Partial'], ['Overdue', 'Overdue'], ['Unpaid', 'Unpaid']];
    options.forEach(function (option) { state.add(new Option(option[1], option[0])); });
    var entity = document.createElement('select');
    entity.className = 'form-select';
    entity.setAttribute('aria-label', kind === 'customers' ? 'Company' : 'Customer');
    entity.add(new Option(kind === 'customers' ? 'All companies' : 'All customers', ''));
    function entityName(row) { var cell = row.cells[kind === 'customers' ? 1 : 0]; return (cell.querySelector('span') || cell).textContent.trim(); }
    Array.from(new Set(api.rows().nodes().toArray().map(entityName))).sort().forEach(function (name) { entity.add(new Option(name, name)); });
    controls.append(state, entity);
    var reset = document.createElement('button');
    reset.type = 'button'; reset.className = 'btn btn-outline-secondary'; reset.textContent = 'Reset'; reset.dataset.noLoader = 'true';
    controls.appendChild(reset); bar.appendChild(controls);
    var container = api.table().container();
    container.parentNode.insertBefore(bar, container);
    $(container).find('.dataTables_filter, .dt-search').hide();
    var predicate = function (settings, data, index) {
      if (settings.nTable !== table) return true;
      var row = api.row(index).node();
      if (entity.value && entityName(row) !== entity.value) return false;
      if (!state.value) return true;
      if (kind !== 'customers') return data[6].trim() === state.value;
      var amount = Number(row.cells[3].textContent.replace(/[^0-9.\-]/g, ''));
      return state.value === 'due' ? amount > 0 : state.value === 'credit' ? amount < 0 : amount === 0;
    };
    $.fn.dataTable.ext.search.push(predicate);
    $(table).on('destroy.dt', function () { $.fn.dataTable.ext.search = $.fn.dataTable.ext.search.filter(function (fn) { return fn !== predicate; }); bar.remove(); });
    $(state).add(entity).on('change', function () { api.draw(); });
    reset.onclick = function () { state.value = ''; entity.value = ''; search.value = ''; $(state).add(entity).trigger('change.select2'); api.search('').draw(); };
    if (window.initAgriSelect2) window.initAgriSelect2(bar);
  };
  function initialize() {
    // Use the same themed select controls for the Market Hub filters.
    [
      ['workerStatusFilter', 'workerStatusBtn', 'filterWorkers'],
      ['workerClassFilter', 'workerClassBtn', 'filterWorkers'],
      ['farmerRentalHardwareFilter', 'farmerRentalHardwareBtn', 'filterFarmerRentals']
    ].forEach(function (config) {
      var hidden = document.getElementById(config[0]);
      var button = document.getElementById(config[1]);
      if (!hidden || !button || hidden.tagName !== 'INPUT') return;
      var dropdown = button.closest('.dropdown');
      var select = document.createElement('select');
      select.id = hidden.id;
      select.className = 'form-select';
      select.setAttribute('aria-label', button.textContent.trim());
      dropdown.querySelectorAll('[data-val]').forEach(function (option) { select.add(new Option(option.textContent.trim(), option.dataset.val)); });
      select.value = hidden.value;
      hidden.replaceWith(select);
      dropdown.remove();
      if (window.jQuery) jQuery(select).on('change', function () { if (window[config[2]]) window[config[2]](); });
      if (window.initAgriSelect2) window.initAgriSelect2(select.parentElement);
    });
    if (window.jQuery) {
      // Select2 emits jQuery events; native change listeners (including dashboard
      // date controls) need a real DOM event. Do not bridge change itself.
      jQuery(document).off('.farmerNativeChange').on(
        'select2:select.farmerNativeChange select2:clear.farmerNativeChange',
        'select', function () {
          this.dispatchEvent(new Event('change', { bubbles: true }));
        }
      );
      // Reset handlers often set .value directly. Refresh the displayed labels
      // after those handlers run without triggering another filtering request.
      document.addEventListener('click', function (event) {
        var button = event.target.closest('button, a');
        if (!button || !/reset|clear.*filter/i.test(button.id + ' ' + button.textContent + ' ' + (button.title || ''))) return;
        queueMicrotask(function () {
          jQuery('select.select2-hidden-accessible').trigger('change.select2');
        });
      });
    }

    document.querySelectorAll('.sales-filter-form').forEach(function (form) {
      var dates = form.querySelectorAll('input[type="date"]');
      if (dates.length !== 2) return;
      function validateDates() {
        var valid = !dates[0].value || !dates[1].value || dates[0].value <= dates[1].value;
        dates[1].setCustomValidity(valid ? '' : 'End date must be on or after start date.');
        return valid;
      }
      dates.forEach(function (date) { date.addEventListener('input', validateDates); });
      form.addEventListener('click', function (event) {
        var button = event.target.closest('button');
        if (!button || /reset/i.test(button.id)) return;
        var period = form.querySelector('select[id$="PeriodFilter"]');
        if (period && period.value === 'custom' && !validateDates()) {
          event.preventDefault();
          event.stopImmediatePropagation();
          dates[1].reportValidity();
        }
      }, true);
    });

    document.querySelectorAll('input[placeholder*="search" i]').forEach(function (input) {
      if (input.closest('.modal, .offcanvas, form, .dataTables_wrapper, .dt-container')) return;
      var group = input.closest('.input-group');
      if (!group) return;
      var bar = group.closest('.card-body');
      if (bar && !bar.classList.contains('flex-column') && !bar.querySelector('table, textarea, input[type="file"]')) {
        bar.classList.add('farmer-list-toolbar');
        bar.querySelectorAll('select').forEach(function (select) {
          // Preserve Bootstrap grids used by reports and labelled filter fields.
          var parent = select.parentElement;
          if (parent !== bar && !parent.className.match(/\bcol(?:-|\b)/) &&
              !parent.querySelector('label')) parent.classList.add('farmer-list-filters');
        });
      }
      if (group.closest('.search-collapse-wrapper, .farmer-click-search')) return;
      var wrapper = document.createElement('div');
      wrapper.className = 'farmer-click-search';
      var toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'btn btn-outline-secondary';
      toggle.dataset.noLoader = 'true';
      toggle.setAttribute('aria-label', 'Open search');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.innerHTML = '<i class="ri-search-line" aria-hidden="true"></i>';
      group.parentNode.insertBefore(wrapper, group);
      wrapper.append(toggle, group);
      group.hidden = !input.value;
      toggle.hidden = !!input.value;
      var close = document.createElement('button');
      close.type = 'button';
      close.className = 'btn btn-outline-secondary';
      close.dataset.noLoader = 'true';
      close.setAttribute('aria-label', 'Clear and close search');
      close.innerHTML = '<i class="ri-close-line" aria-hidden="true"></i>';
      group.appendChild(close);
      toggle.addEventListener('click', function () {
        group.hidden = false;
        toggle.hidden = true;
        toggle.setAttribute('aria-expanded', 'true');
        input.focus();
      });
      function clearSearch() {
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('keyup', { bubbles: true }));
        group.hidden = true;
        toggle.hidden = false;
        toggle.setAttribute('aria-expanded', 'false');
        toggle.focus();
      }
      close.addEventListener('click', clearSearch);
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') clearSearch();
      });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
