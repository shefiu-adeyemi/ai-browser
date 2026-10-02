import { describe, expect, it } from 'vitest'
import { parseCsv, rowsFromCsv } from './csv'

describe('parseCsv', () => {
  it('reads quotes, doubled quotes, commas and newlines inside a cell', () => {
    expect(parseCsv('a,b\r\n"x, y","say ""hi"""\n"line1\nline2",z')).toEqual([['a', 'b'], ['x, y', 'say "hi"'], ['line1\nline2', 'z']])
  })

  it('copes with a byte-order mark, blank lines, a last line with no newline and old Mac line ends', () => {
    expect(parseCsv('﻿a,b\n\n1,2\n\n3,4')).toEqual([['a', 'b'], ['1', '2'], ['3', '4']])
    expect(parseCsv('a,b\r1,2\r3,4')).toEqual([['a', 'b'], ['1', '2'], ['3', '4']])
  })

  it('picks the separator from the first line', () => {
    expect(parseCsv('a;b\n1;2')).toEqual([['a', 'b'], ['1', '2']])
    expect(parseCsv('a\tb\n1\t2')).toEqual([['a', 'b'], ['1', '2']])
    expect(parseCsv('a,b;c\n1,2;3')).toEqual([['a', 'b;c'], ['1', '2;3']])
  })

  it('trims cells and drops rows that are empty', () => {
    expect(parseCsv(' a , b \n , \n 1 , 2 ')).toEqual([['a', 'b'], ['1', '2']])
  })

  it('is fine with nothing, and with an unclosed quote', () => {
    expect(parseCsv('')).toEqual([])
    expect(parseCsv('"never closed,1\n2')).toEqual([['never closed,1\n2']])
  })

  it('keeps a formula as plain text', () => {
    expect(parseCsv('a\n=1+1')).toEqual([['a'], ['=1+1']])
  })
})

describe('rowsFromCsv', () => {
  const inputs = [{ name: 'profile_url', label: 'Profile address' }, { name: 'note' }]

  it('maps a header to the inputs by name or label, ignoring case and punctuation', () => {
    const out = rowsFromCsv('Profile Address, NOTE ,extra\nhttps://a.test/1,hi,zzz\nhttps://a.test/2,,', inputs)
    expect(out.header).toBe(true)
    expect(out.rows).toEqual([{ profile_url: 'https://a.test/1', note: 'hi' }, { profile_url: 'https://a.test/2', note: '' }])
    expect(out.ignored).toEqual(['extra'])
  })

  it('with no header and one input, each line is a value', () => {
    const out = rowsFromCsv('https://a.test/1\nhttps://a.test/2', [{ name: 'profile_url' }])
    expect(out.header).toBe(false)
    expect(out.rows).toEqual([{ profile_url: 'https://a.test/1' }, { profile_url: 'https://a.test/2' }])
  })

  it('with no header and several inputs, the columns are taken in order', () => {
    expect(rowsFromCsv('https://a.test/1,hello', inputs).rows).toEqual([{ profile_url: 'https://a.test/1', note: 'hello' }])
  })

  it('a line that looks like data is not mistaken for a header', () => {
    const out = rowsFromCsv('https://a.test/1\nhttps://a.test/2', inputs)
    expect(out.header).toBe(false)
    expect(out.rows).toHaveLength(2)
  })

  it('nothing to read or nowhere to put it gives no rows', () => {
    expect(rowsFromCsv('', inputs).rows).toEqual([])
    expect(rowsFromCsv('a,b', []).rows).toEqual([])
  })

  it('skips rows with no values at all', () => {
    expect(rowsFromCsv('profile_url\n\n,\nx', inputs).rows).toEqual([{ profile_url: 'x' }])
  })
})
