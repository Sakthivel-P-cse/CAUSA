import React from 'react'
import { flexRender, getCoreRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react'
import { EmptyState } from './Feedback.jsx'

export function DataTable({ data = [], columns = [], caption = 'Data', getRowId, emptyMessage = 'No results' }) {
  const table = useReactTable({ data, columns, getRowId, getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel() })
  if (!data.length) return <EmptyState title={emptyMessage} />
  return (
    <div className="ui-table-wrap">
      <table className="ui-table">
        <caption className="sr-only">{caption}</caption>
        <thead>{table.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map(header => {
          const sorted = header.column.getIsSorted()
          return <th key={header.id} scope="col" aria-sort={sorted ? (sorted === 'asc' ? 'ascending' : 'descending') : 'none'}>
            {header.isPlaceholder ? null : header.column.getCanSort() ? (
              <button type="button" onClick={header.column.getToggleSortingHandler()}>
                {flexRender(header.column.columnDef.header, header.getContext())}
                {sorted === 'asc' ? <ArrowUp size={14} /> : sorted === 'desc' ? <ArrowDown size={14} /> : <ChevronsUpDown size={14} />}
              </button>
            ) : flexRender(header.column.columnDef.header, header.getContext())}
          </th>
        })}</tr>)}</thead>
        <tbody>{table.getRowModel().rows.map(row => <tr key={row.id}>{row.getVisibleCells().map((cell, index) => (
          <td key={cell.id} data-label={String(cell.column.columnDef.header || '')}>{index === 0 ? <span className="sr-only">Row {row.index + 1}: </span> : null}{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
        ))}</tr>)}</tbody>
      </table>
    </div>
  )
}

